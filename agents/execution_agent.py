"""Execution Agent — places orders on Groww (paper or live)."""

import threading
import time
import os
import requests
from datetime import datetime, time as dtime
import pytz

from core.shared_state import STATE
from core.messenger     import Messenger
from agents.learning_agent import BRAIN

IST = pytz.timezone('Asia/Kolkata')
BLOCK_ON_FILTER_ERROR = os.getenv('BLOCK_ON_FILTER_ERROR', 'true').lower() == 'true'

class ExecutionAgent(threading.Thread):

    def __init__(self, messenger: Messenger):
        super().__init__(daemon=True, name='ExecutionAgent')
        self.messenger = messenger
        self.paper     = os.getenv('PAPER_MODE', 'true').lower() == 'true'
        self.trade_counter = 0

    def calculate_trade_params(self, signal: dict) -> dict:
        """Build entry params — auto-switch to affordable OTM strike if needed."""
        from src.capital_guard import compute_lots, LIVE_CAPITAL_RS, check_trade_cost_vs_capital
        from src.premium_feed import fetch_option_ltp
        from src.strike_picker import find_affordable_strike, format_strike_switch

        price   = signal.get('price', 0)
        atr     = STATE.get('market.atr', 500)
        zone    = STATE.get('zone') or {}
        bias    = signal.get('trend', zone.get('bias', 'BULLISH'))
        strike  = zone.get('strike', 0)
        opt     = zone.get('opt_type', 'CE')
        expiry  = zone.get('expiry', '')
        name    = zone.get('option_name', '')
        premium = zone.get('premium', 265)
        strike_note = ''

        from src.expiry_picker import days_to_expiry, next_banknifty_expiry
        min_exp_days = int(os.getenv('MIN_DAYS_TO_EXPIRY', '5'))
        if not expiry or days_to_expiry(expiry) < min_exp_days:
            expiry = next_banknifty_expiry(min_exp_days)
            strike_note = f'Using {min_exp_days}d+ expiry (theta safety)'

        if strike and expiry:
            live_prem = fetch_option_ltp(strike, opt, expiry)
            if live_prem > 0:
                premium = round(live_prem, 0)

        old_snap = {'name': name or f'BANKNIFTY {strike} {opt}', 'premium': premium}
        lot_cost = premium * 15
        if check_trade_cost_vs_capital(lot_cost)['blocked']:
            alt = find_affordable_strike(price, bias, expiry)
            if not alt:
                return {}
            strike_note = format_strike_switch(old_snap, alt, LIVE_CAPITAL_RS)
            strike  = alt['strike']
            opt     = alt['opt_type']
            name    = alt['name']
            premium = alt['premium']
            STATE.set('trade.strike_switch_note', strike_note)

        dyn = STATE.get('trade.dynamic_sl')
        if dyn:
            sl_prem, tgt_prem = dyn['sl_prem'], dyn['tgt_prem']
            sl_pct, tgt_mul   = dyn['sl_pct'], dyn['tgt_mul']
        else:
            if   atr < 486:  sl_pct, tgt_mul = 0.25, 2.5
            elif atr < 875:  sl_pct, tgt_mul = 0.30, 2.0
            elif atr < 1159: sl_pct, tgt_mul = 0.35, 2.0
            else:            sl_pct, tgt_mul = 0.40, 1.8
            sl_prem  = round(premium * (1 - sl_pct), 0)
            tgt_prem = round(premium * tgt_mul, 0)

        brain = STATE.get('brain', {})
        widen = brain.get('sl_widen_pct', 0)
        rec_sl = STATE.get('recovery.sl_pct_override')
        if rec_sl and STATE.get('recovery', {}).get('pending_recovery_trade'):
            sl_pct = min(sl_pct, float(rec_sl))
            sl_prem = round(premium * (1 - sl_pct), 0)
        elif widen and not dyn:
            sl_prem = round(premium * (1 - sl_pct * (1 + widen)), 0)

        lots  = compute_lots(brain.get('kelly', 0.25), brain.get('total_trades', 0))
        qty   = lots * 15
        leg1_profit = round((premium * 1.5 - premium) * (qty // 2), 0)

        return {
            'name':         name or f'BANKNIFTY {strike} {opt}',
            'strike':       strike,
            'opt_type':     opt,
            'expiry':       expiry,
            'premium':      premium,
            'sl_prem':      sl_prem,
            'tgt_prem':     tgt_prem,
            'lots':         lots,
            'qty':          qty,
            'lot_cost':     premium * qty,
            'max_loss':     round(premium * sl_pct * qty, 0),
            'max_gain':     round(premium * (tgt_mul - 1) * qty, 0),
            'leg1_profit':  leg1_profit,
            'strike_note':  strike_note,
        }

    def _groww_balance_line(self, required: float) -> str:
        """Show wallet status on every trade suggestion."""
        token = STATE.get('system.groww_token', '')
        if not token:
            try:
                from src.groww_auth import fetch_groww_token
                token = fetch_groww_token()
            except Exception:
                pass
        from src.safety import check_groww_balance
        paper = self.paper
        bal = check_groww_balance(
            token, required_amount=required,
            fail_open=paper,
        )
        if paper:
            return (
                f"\n🏦 *Groww wallet (preview):* {bal['reason']}\n"
                f"_Paper mode — no real order. Add ₹5k before live._"
            )
        return f"\n🏦 *Groww wallet:* {bal['reason']}"

    def _pre_trade_checks(self, signal: dict, params: dict) -> dict:
        """Safety, premium sanity, liquidity — all must pass."""
        from src.safety import run_safety_checks
        from src.trade_analytics import check_premium_sanity, check_liquidity
        from src.salary_trader_guards import run_salary_trader_guards

        guard = run_salary_trader_guards(signal, params)
        if not guard.get('ok', True):
            return {'ok': False, 'reason': guard['reason']}

        paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
        token = '' if paper else (
            STATE.get('system.groww_token', '') or os.getenv('GROWW_ACCESS_TOKEN', '')
        )
        safety = run_safety_checks(
            groww_token=token,
            current_price=signal.get('price', 0),
            zone=STATE.get('zone'),
            required_balance=params.get('lot_cost', 5000),
        )
        if not safety.get('safe'):
            return {'ok': False, 'reason': safety.get('reason', 'Safety check failed')}

        prem = check_premium_sanity(params.get('premium', 0), params.get('lot_cost', 0))
        if not prem['ok']:
            return {'ok': False, 'reason': prem['reason']}

        from src.wr_filters import check_premium_sweet_spot
        sweet = check_premium_sweet_spot(params.get('premium', 0))
        if not sweet['ok']:
            return {'ok': False, 'reason': sweet['reason']}

        liq = check_liquidity(
            params.get('strike', 0),
            params.get('opt_type', 'CE'),
            params.get('expiry', ''),
            params.get('premium', 0),
        )
        if not liq['ok']:
            return {'ok': False, 'reason': liq['reason']}

        return {'ok': True, 'reason': 'All pre-trade checks passed ✅'}

    def _reject_fake_live_fill(self, result: dict) -> dict:
        """Block paper/fake fills when PAPER_MODE=false."""
        if self.paper or not result.get('success'):
            return result
        if result.get('paper'):
            self.messenger.send(
                "🛑 *Live order blocked*\n\n"
                "Groww returned a paper/fake fill in live mode.\n"
                "_No position opened — check /groww and retry._"
            )
            return {'success': False, 'error': 'Fake paper fill rejected in live mode'}
        oid = str(result.get('order_id', ''))
        if oid.startswith('PAPER_'):
            self.messenger.send(
                "🛑 *Live order blocked*\n\n"
                f"Order id `{oid}` is paper — not a real Groww fill."
            )
            return {'success': False, 'error': 'PAPER_ order id rejected in live mode'}
        return result

    def place_order(self, params: dict) -> dict:
        """Execute order via Groww API"""
        lot_cost = params.get('lot_cost', 4500)
        if not self.paper:
            from src.brain_metrics import assess_live_readiness
            from src.capital_guard import LIVE_CAPITAL_RS
            ready = assess_live_readiness()
            if not ready['ready']:
                self.messenger.send(
                    f"🛑 *Live order blocked*\n\n"
                    f"{ready['reason']}\n\n"
                    f"Set `PAPER_MODE=true` and complete paper period first.\n"
                    f"Type /readiness for full gate checklist."
                )
                return {'success': False, 'error': 'Not live-ready — paper gates not passed'}
        if self.paper:
            self.trade_counter += 1
            return {
                'success':  True,
                'order_id': f"PAPER_{self.trade_counter:04d}",
                'paper':    True
            }
        token = STATE.get('system.groww_token', '') or os.getenv('GROWW_ACCESS_TOKEN', '')
        from src.safety import check_groww_balance
        bal = check_groww_balance(token, required_amount=lot_cost, fail_open=False)
        if not bal.get('sufficient'):
            self.messenger.send(
                f"❌ *Order blocked — insufficient balance*\n\n"
                f"{bal['reason']}\n\n"
                f"Trade cost: ₹{lot_cost:,.0f}\n"
                f"_Add funds to Groww F&O wallet, then /resume_"
            )
            return {'success': False, 'error': bal['reason']}
        try:
            from src.groww_trader import GrowwTrader
            from src.safety import verify_order_filled
            trader = GrowwTrader(token)
            result = trader.buy_option(
                'BANKNIFTY',
                params['strike'],
                params['opt_type'],
                params['expiry'],
                params['sl_prem'],
                params['tgt_prem'],
                lots=params.get('lots', 1)
            )
            if result.get('success'):
                fill = verify_order_filled(token, result.get('order_id', ''))
                if not fill.get('filled'):
                    self.messenger.send(
                        f"❌ *Buy not filled*\n{fill.get('reason', 'Timeout')}\n"
                        f"Check Groww orders — do not assume position is open."
                    )
                    return {'success': False, 'error': fill.get('reason', 'Not filled')}
                result['filled_qty'] = fill.get('qty', params.get('qty', 15))
                if fill.get('price'):
                    result['fill_price'] = fill['price']
                if not result.get('oco_ok'):
                    self.messenger.send(
                        "⚠️ *OCO not confirmed on Groww*\n"
                        "Monitor Agent will manage exits manually."
                    )
            return self._reject_fake_live_fill(result)
        except Exception as e:
            error_str = str(e).lower()
            # If token expired, refresh and retry once
            if 'auth' in error_str or 'expired' in error_str or 'invalid' in error_str:
                print(f"🔄 Token expired during order: {str(e)[:40]}")
                try:
                    from src.groww_auth import fetch_groww_token
                    fresh_token = fetch_groww_token(force_refresh=True)
                    STATE.set('system.groww_token', fresh_token)

                    from src.groww_trader import GrowwTrader
                    from src.safety import verify_order_filled
                    trader = GrowwTrader(fresh_token)
                    result = trader.buy_option(
                        'BANKNIFTY',
                        params['strike'],
                        params['opt_type'],
                        params['expiry'],
                        params['sl_prem'],
                        params['tgt_prem'],
                        lots=params.get('lots', 1)
                    )
                    if result.get('success'):
                        fill = verify_order_filled(fresh_token, result.get('order_id', ''))
                        if not fill.get('filled'):
                            return {'success': False, 'error': fill.get('reason', 'Not filled')}
                        result['filled_qty'] = fill.get('qty', params.get('qty', 15))
                    if result.get('success'):
                        print("✅ Order placed after token refresh")
                        return self._reject_fake_live_fill(result)
                except Exception:
                    pass

            return {'success': False, 'error': str(e)}

    def send_trade_suggestion(self, signal: dict, risk: dict, params: dict):
        """Send Telegram trade suggestion — user must confirm before entry."""
        trend   = signal.get('trend', 'NEUTRAL')
        score   = signal.get('score', 0)
        price   = signal.get('price', 0)
        regime  = signal.get('regime', '')
        session = signal.get('session', '')
        bias_e  = '🟢' if trend == 'BULLISH' else '🔴'
        stars   = '⭐' * min(score, 5)
        mode    = "📝 Paper" if self.paper else "💸 Live"

        risk_reasons = '\n'.join(
            f"  {r}" for r in
            (signal.get('reasons', [])[:3] + risk.get('reasons', [])[:2])
        )
        warnings = '\n'.join(
            f"  {w}" for w in risk.get('warnings', [])[:2] if w
        )

        msg = (
            f"📋 *TRADE SUGGESTION — {mode}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{bias_e} {trend} | Score {score} {stars}\n"
            f"BNF: {price:,.0f} | {session} | {regime}\n\n"
            f"Option: *{params['name']}*\n"
            f"Premium: ₹{params['premium']}/unit\n"
            f"Cost:    ₹{params['lot_cost']:,} ({params.get('lots', 1)} lot)\n\n"
            f"🛑 SL:     ₹{params['sl_prem']}\n"
            f"🎯 Target: ₹{params['tgt_prem']}\n"
            f"📊 Max Loss:  ₹{params['max_loss']:,}\n"
            f"💰 Max Gain:  ₹{params['max_gain']:,}\n\n"
            f"📋 Why:\n{risk_reasons}\n"
        )
        if warnings:
            msg += f"\n⚠️ Notes:\n{warnings}\n"
        if params.get('strike_note'):
            msg += f"\n{params['strike_note']}\n"
        from src.trade_analytics import format_breakeven_line
        be_line = format_breakeven_line(params)
        if be_line:
            msg += f"\n{be_line}\n"
        brain_note = STATE.get('brain.auto_rule_note', '')
        if brain_note:
            msg += f"\n🧠 _{brain_note}_\n"
        flow = STATE.get('signals.market_flow') or STATE.get('market.flow')
        if flow:
            from src.market_flow import format_flow_compact
            msg += format_flow_compact(flow)
        try:
            from src.llm_advisor import llm_enabled, explain_trade_setup
            if llm_enabled():
                ai = explain_trade_setup(signal, risk)
                if ai:
                    msg += f"\n\n🤖 *AI coach:*\n{ai}\n"
        except Exception:
            pass
        try:
            from src.trade_probability import format_probability_line
            from src.brain_metrics import compute_paper_confidence
            from src.learning_scoreboard import shadow_vs_paper_stats
            msg += f"\n{format_probability_line(signal)}\n"
            conf = compute_paper_confidence()
            sh = shadow_vs_paper_stats(7)
            msg += (
                f"📈 Paper conf: {conf['score']}/100 | "
                f"Shadow 7d: {sh.get('shadow_wr', 0)}% ({sh.get('shadow_n', 0)} drills)\n"
            )
        except Exception:
            pass
        msg += (
            f"\n💰 *Min profit (leg 1 @ 1.5×):* ~₹{params.get('leg1_profit', 0):,}\n"
            f"_Confidence: {risk.get('confidence', 0)}%_"
        )
        msg += self._groww_balance_line(params.get('lot_cost', 4000))
        msg += "\nTap a button or type /execute or /skip"

        buttons = [[
            {'text': '✅ Execute (Paper)', 'callback_data': 'trade_execute'},
            {'text': '⏭ Skip', 'callback_data': 'trade_skip'},
        ]]
        if not self.paper:
            buttons[0][0]['text'] = '✅ Execute (Live)'

        self.messenger.send_with_buttons(msg, buttons)
        from src.trade_analytics import log_funnel
        log_funnel('suggested', signal)
        STATE.set('signals.pending_params', params)

    def run(self):
        STATE.set_agent_status('execution', 'RUNNING')
        print("⚡ Execution Agent started")

        while STATE.get('system.running'):
            try:
                # ── Awaiting user confirmation ────────────────────
                if (STATE.get('signals.awaiting_confirmation')
                        and not STATE.get('signals.confirmation_sent')
                        and not STATE.get('position.open')):
                    signal = STATE.get('signals.analysis')
                    risk   = STATE.get('signals.risk')
                    params = self.calculate_trade_params(signal)

                    if not params.get('name'):
                        STATE.set('signals.awaiting_confirmation', False)
                        continue

                    chk = self._pre_trade_checks(signal, params)
                    if not chk['ok']:
                        STATE.set('signals.awaiting_confirmation', False)
                        self.messenger.send(
                            f"❌ *Trade blocked before suggestion*\n{chk['reason']}"
                        )
                        continue

                    self.send_trade_suggestion(signal, risk, params)
                    STATE.set('signals.confirmation_sent', True)
                    continue

                if STATE.get('signals.execute_now') and not STATE.get('position.open'):
                    signal   = STATE.get('signals.analysis')
                    risk     = STATE.get('signals.risk')
                    params   = self.calculate_trade_params(signal)

                    # Clear execute signal immediately (prevent double entry)
                    STATE.set('signals.execute_now', False)
                    STATE.set('signals.confirmation_sent', False)
                    STATE.set('signals.awaiting_confirmation', False)

                    if not params.get('name'):
                        continue

                    chk = self._pre_trade_checks(signal, params)
                    if not chk['ok']:
                        self.messenger.send(
                            f"❌ *Execute blocked*\n{chk['reason']}"
                        )
                        continue

                    try:
                        from src.shadow_learning import should_auto_paper_execute
                        from src.trade_probability import format_probability_line
                        if should_auto_paper_execute():
                            self.messenger.send(
                                f"🎓 *Auto paper entry* (learning phase)\n"
                                f"━━━━━━━━━━━━━━━━━━━\n"
                                f"{signal.get('trend')} score {signal.get('score')} | "
                                f"{params['name']}\n"
                                f"Premium ~₹{params['premium']} | "
                                f"SL ₹{params['sl_prem']} → Tgt ₹{params['tgt_prem']}\n"
                                f"{format_probability_line(signal)}\n"
                                f"_All filters passed — bot entering virtual trade to learn_"
                            )
                    except Exception:
                        pass

                    # Record entry in brain
                    market_ctx = {
                        'bias':         signal.get('trend'),
                        'session':      signal.get('session'),
                        'bnf_price':    signal.get('price'),
                        'score':        signal.get('score'),
                        'regime':       signal.get('regime'),
                        'rsi':          signal.get('rsi'),
                        'volume_ratio': 1.0,
                    }
                    trade_for_brain = {
                        'name':       params['name'],
                        'entry_prem': params['premium'],
                        'sl_prem':    params['sl_prem'],
                        'tgt_prem':   params['tgt_prem'],
                    }
                    learning_id = BRAIN.record_entry(trade_for_brain, market_ctx)
                    try:
                        from src.feature_log import capture_entry_features
                        capture_entry_features(learning_id, signal, params)
                    except Exception:
                        pass

                    # Place order
                    result = self.place_order(params)

                    if result.get('success'):
                        from src.trade_analytics import log_funnel
                        from src.position_store import save_position
                        from src.groww_symbols import groww_option_symbol
                        log_funnel('executed', signal)
                        STATE.set('signals.pending_params', None)
                        qty = params.get('qty', 15)
                        if result.get('filled_qty'):
                            qty = int(result['filled_qty'])
                        fill_prem = result.get('fill_price') or params['premium']
                        contract_id = result.get('contract_id') or groww_option_symbol(
                            'BANKNIFTY', params['strike'], params['opt_type'], params['expiry']
                        )
                        from src.zone_manager import mark_zone_used
                        mark_zone_used()
                        STATE.set('trade.dynamic_sl', None)

                        # Update position state
                        STATE.update('position', {
                            'open':         True,
                            'name':         params['name'],
                            'entry_price':  fill_prem,
                            'entry_time':   datetime.now(IST).strftime('%H:%M'),
                            'sl_prem':      params['sl_prem'],
                            'tgt_prem':     params['tgt_prem'],
                            'trail_sl':     params['sl_prem'],
                            'peak_premium': fill_prem,
                            'leg1_done':    False,
                            'leg1_profit':  0,
                            'qty':          qty,
                            'opt_type':     params.get('opt_type', 'CE'),
                            'strike':       params.get('strike', 0),
                            'expiry':       params.get('expiry', ''),
                            'contract_id':  contract_id,
                            'oco_ok':       result.get('oco_ok', False),
                            'learning_id':  learning_id,
                            'bnf_at_entry': signal.get('price', 0),
                            'mae_rs':       0,
                            'mfe_rs':       0,
                        })
                        save_position(STATE.get('position'))

                        # Update brain trades today
                        trades_today = STATE.get('brain.trades_today', 0)
                        STATE.set('brain.trades_today', trades_today + 1)

                        if STATE.get('recovery', {}).get('pending_recovery_trade'):
                            try:
                                from src.loss_recovery import mark_recovery_used
                                mark_recovery_used()
                            except Exception:
                                pass

                        # Mark zone used
                        STATE.set('zone.used', True)
                        STATE.set('zone.active', False)

                        # Send Telegram
                        if self.paper:
                            from src.paper_journal import format_paper_entry
                            self.messenger.send(
                                format_paper_entry(learning_id, params, signal)
                            )
                        else:
                            mode = "💸 LIVE"
                            risk_reasons = '\n'.join(
                                f"  {r}" for r in
                                (signal.get('reasons', [])[:3] + risk.get('reasons', [])[:2])
                            )
                            msg = (
                                f"⚡ *ENTRY EXECUTED — {mode}*\n"
                                f"━━━━━━━━━━━━━━━━━━━\n"
                                f"Option: *{params['name']}*\n"
                                f"Premium: ₹{params['premium']}/unit\n"
                                f"Cost:    ₹{params['lot_cost']:,} ({params.get('lots', 1)} lot)\n\n"
                                f"🛑 SL:     ₹{params['sl_prem']}\n"
                                f"🎯 Target: ₹{params['tgt_prem']}\n"
                                f"📊 Max Loss:  ₹{params['max_loss']:,}\n"
                                f"💰 Max Gain:  ₹{params['max_gain']:,}\n\n"
                                f"📋 Why:\n{risk_reasons}\n\n"
                                f"Order ID: `{result['order_id']}`\n"
                                f"_Monitor Agent watching every 30s_ 🤖"
                            )
                            self.messenger.send(msg)

                    else:
                        STATE.set('brain.trades_today',
                                  STATE.get('brain.trades_today', 1) - 1)
                        self.messenger.send(
                            f"❌ Order failed: {result.get('error', 'Unknown')}\n"
                            f"Place manually on Groww."
                        )

            except Exception as e:
                STATE.add_error(f"Execution Agent: {str(e)[:60]}")

            time.sleep(5)

        STATE.set_agent_status('execution', 'STOPPED')


# ══════════════════════════════════════════════════════════════════
# MONITOR AGENT
# ══════════════════════════════════════════════════════════════════

