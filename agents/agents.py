"""
Risk Agent — Approves or Rejects every trade
Execution Agent — Places orders on Groww
Monitor Agent — Watches position every 30 seconds
"""

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


# ══════════════════════════════════════════════════════════════════
# RISK AGENT
# ══════════════════════════════════════════════════════════════════

class RiskAgent(threading.Thread):

    def __init__(self, messenger: Messenger):
        super().__init__(daemon=True, name='RiskAgent')
        self.messenger = messenger

    def approve(self, signal: dict) -> dict:
        """Run all risk checks. Return GO or NO-GO."""
        reasons  = []
        warnings = []
        price    = signal.get('price', 0)
        score    = signal.get('score', 0)
        trend    = signal.get('trend', 'NEUTRAL')
        session  = signal.get('session', '')
        regime   = signal.get('regime', '')
        rsi      = signal.get('rsi', 50)

        from src.trade_analytics import session_expectancy
        sess_exp = session_expectancy()
        if session in sess_exp and sess_exp[session]['trades'] >= 5:
            if sess_exp[session]['expectancy'] < -150:
                return {
                    'approved': False,
                    'reason': (
                        f"🧠 {session} expectancy ₹{sess_exp[session]['expectancy']}/trade "
                        f"— brain blocks this session"
                    ),
                }

        # ── Capital loss caps (salary trader protection) ──────────
        from src.capital_guard import check_daily_loss_cap, check_weekly_loss_cap
        daily_cap = check_daily_loss_cap()
        if daily_cap['blocked']:
            return {'approved': False, 'reason': daily_cap['reason']}
        weekly_cap = check_weekly_loss_cap()
        if weekly_cap['blocked']:
            STATE.set('system.paused', True)
            return {'approved': False, 'reason': weekly_cap['reason']}

        zone = STATE.get('zone', {})
        est_prem = zone.get('premium', 265)
        est_params = {
            'premium':  est_prem,
            'lot_cost': est_prem * 15,
            'max_loss': round(est_prem * 0.30 * 15, 0),
            'expiry':   zone.get('expiry', ''),
            'strike':   zone.get('strike', 0),
            'opt_type': zone.get('opt_type', 'CE'),
        }
        from src.salary_trader_guards import run_salary_trader_guards
        guard = run_salary_trader_guards(signal, est_params)
        if not guard.get('ok', True):
            return {'approved': False, 'reason': guard['reason']}
        warnings.extend(guard.get('warnings', []))

        # ── Event calendar (RBI, Fed, monthly expiry) ─────────────
        from src.trade_filters import is_event_day
        event = is_event_day()
        if event.get('skip'):
            return {'approved': False, 'reason': event['reason']}
        if event.get('caution'):
            warnings.append(event['reason'])

        # ── Manual pause check ────────────────────────────────────
        from src.capital_guard import check_trade_cost_vs_capital, LIVE_CAPITAL_RS
        from src.premium_feed import fetch_option_ltp
        from src.strike_picker import find_affordable_strike
        premium = zone.get('premium', 265)
        strike  = zone.get('strike', 0)
        opt     = zone.get('opt_type', 'CE')
        expiry  = zone.get('expiry', '')
        price   = signal.get('price', 0)
        bias    = signal.get('trend', zone.get('bias', 'BULLISH'))

        if strike and expiry:
            live_p = fetch_option_ltp(strike, opt, expiry)
            if live_p > 0:
                premium = live_p

        est_cost = premium * 15
        cost_chk = check_trade_cost_vs_capital(est_cost)
        if cost_chk['blocked']:
            alt = find_affordable_strike(price, bias, expiry)
            if alt:
                STATE.set('trade.strike_switch', alt)
                # Allow — execution will use cheaper strike
                warnings.append(
                    f"💡 Default strike too costly — will use {alt['name']} "
                    f"(₹{alt['lot_cost']:,}/lot)"
                )
            else:
                return {
                    'approved': False,
                    'reason': (
                        f'🛑 No affordable strike for ₹{LIVE_CAPITAL_RS:,.0f} capital. '
                        f'Cheapest OTM still > ₹{LIVE_CAPITAL_RS*0.8:,.0f}. Skip today.'
                    ),
                }

        # ── Manual pause check ────────────────────────────────────
        if STATE.get('system.paused'):
            return {
                'approved': False,
                'reason':   '⏸ Bot paused manually — type /resume to continue'
            }

        # ── Consecutive loss circuit breaker ──────────────────────
        # 2 losses in a week = auto pause. Protect capital.
        weekly_losses = STATE.get('system.weekly_losses', 0)
        if weekly_losses >= 2:
            STATE.set('system.paused', True)
            STATE.set('system.pause_reason', 'Auto-paused: 2 consecutive losses')
            return {
                'approved': False,
                'reason':   (
                    '🛑 2 consecutive losses this week — auto paused.\n'
                    'Review and type /resume when ready.'
                )
            }

        # ── Brain check ───────────────────────────────────────────
        brain        = STATE.get('brain')
        min_score    = brain.get('min_score', 5)
        from src.brain_metrics import get_dynamic_min_score, check_pattern_combo
        dyn_score    = get_dynamic_min_score(min_score)
        if dyn_score > min_score:
            warnings.append(
                f"🧠 Recent paper weak — min score raised to {dyn_score}"
            )
            min_score = dyn_score
        max_trades   = brain.get('max_trades_day', 1)
        trades_today = brain.get('trades_today', 0)
        avoid_hours  = brain.get('avoid_hours', [])
        hour         = datetime.now(IST).hour

        combo = check_pattern_combo(session, hour, score, regime)
        if combo['block']:
            return {'approved': False, 'reason': combo['reason']}

        if score < min_score:
            return {'approved': False,
                    'reason': f"Score {score} < brain min {min_score}"}

        if trades_today >= max_trades:
            return {'approved': False,
                    'reason': f"Max trades/day reached ({trades_today}/{max_trades})"}

        if hour in avoid_hours:
            return {'approved': False,
                    'reason': f"Hour {hour}:00 historically bad — brain says skip"}

        # ── Pattern confidence check ──────────────────────────────
        day     = datetime.now(IST).strftime('%A')
        hour_wr = BRAIN.get_pattern_winrate(f"hour:{hour}")
        day_wr  = BRAIN.get_pattern_winrate(f"day:{day}")
        reg_wr  = BRAIN.get_pattern_winrate(f"regime:{regime}")

        if hour_wr is not None and hour_wr < 35:
            return {'approved': False,
                    'reason': f"Hour {hour}:00 win rate {hour_wr:.0f}% (from history)"}

        if hour_wr and hour_wr >= 65:
            reasons.append(f"✅ Hour {hour}:00 win rate {hour_wr:.0f}%")

        if day_wr and day_wr >= 65:
            reasons.append(f"✅ {day} win rate {day_wr:.0f}%")

        # ── RSI check ─────────────────────────────────────────────
        if trend == 'BULLISH' and rsi > 75:
            return {'approved': False,
                    'reason': f"RSI {rsi} overbought — don't buy CE"}
        if trend == 'BEARISH' and rsi < 25:
            return {'approved': False,
                    'reason': f"RSI {rsi} oversold — don't buy PE"}

        # ── Regime check ──────────────────────────────────────────
        if STATE.get('brain', {}).get('block_ranging') and regime == 'RANGING':
            return {'approved': False,
                    'reason': '🧠 Brain blocked RANGING — this pattern keeps losing'}
        if regime == 'TIGHT_RANGE':
            return {'approved': False, 'reason': "Market in tight range — no edge"}
        if regime == 'RANGING':
            warnings.append("⚠️ Ranging market — proceed carefully")

        # ── Master filters (volume, ATR, global market) ───────────
        try:
            import pandas as pd
            from src.trade_filters import run_all_filters
            zone    = STATE.get('zone', {})
            premium = zone.get('premium', 265)
            c15     = STATE.get('market.candles_15m', [])
            if len(c15) >= 10:
                df = pd.DataFrame(c15)
                df = df.rename(columns={
                    'open': 'Open', 'high': 'High',
                    'low': 'Low', 'close': 'Close', 'volume': 'Volume',
                })
                filt = run_all_filters(trend, df, premium, score)
                if not filt.get('proceed'):
                    return {
                        'approved': False,
                        'reason':   filt.get('reason', 'Filter blocked'),
                    }
                for r in filt.get('reasons', []):
                    if r:
                        reasons.append(r)
                for w in filt.get('warnings', []):
                    if w:
                        warnings.append(w)
                if filt.get('dynamic_sl'):
                    STATE.set('trade.dynamic_sl', filt['dynamic_sl'])
        except Exception as e:
            warnings.append(f"⚠️ Trade filters skipped: {str(e)[:40]}")

        # ── After 2 PM no new entries ─────────────────────────────
        if datetime.now(IST).time() >= dtime(14, 0):
            return {'approved': False, 'reason': "After 2 PM — no new entries"}

        # ── Market Validator (VIX + OI + EMA) ────────────────────
        try:
            from src.market_validator import validate_trade
            validation = validate_trade(trend, price)

            if validation.get('blocked'):
                return {
                    'approved': False,
                    'reason':   validation['block_reason']
                }

            # Add validator score and reasons
            score += validation['score_boost']
            for r in validation['reasons']:
                if r:
                    reasons.append(r)

            warnings.append(validation.get('summary', ''))

        except Exception as e:
            warnings.append(f"⚠️ Validator skipped: {str(e)[:40]}")

        # ── Chart S/R + unified F&O flow (OI walls, theta, swing lines) ──
        try:
            from src.market_flow import flow_allows_trade
            from src.chart_levels import check_chart_levels
            flow_chk = flow_allows_trade(trend, price)
            flow = flow_chk.get('flow', {})
            STATE.set('signals.market_flow', flow)
            if not flow_chk.get('ok', True):
                return {'approved': False, 'reason': flow_chk['reason']}
            chart = flow.get('chart', {})
            chart_chk = check_chart_levels(price, trend, chart)
            if not chart_chk.get('ok', True):
                return {'approved': False, 'reason': chart_chk['reason']}
            score += chart_chk.get('score_delta', 0)
            warnings.extend(chart_chk.get('warnings', []))
            for w in flow_chk.get('warnings', []):
                if w:
                    warnings.append(w)
        except Exception as e:
            warnings.append(f"⚠️ Flow check skipped: {str(e)[:40]}")

        return {
            'approved':   True,
            'reasons':    reasons,
            'warnings':   warnings,
            'confidence': min(50 + score * 5, 95)
        }

    def _needs_confirmation(self) -> bool:
        confirm = os.getenv('CONFIRM_BEFORE_TRADE', 'auto').lower()
        if confirm == 'auto':
            return os.getenv('PAPER_MODE', 'true').lower() == 'true'
        return confirm == 'true'

    def run(self):
        STATE.set_agent_status('risk', 'RUNNING')
        print("🛡️ Risk Agent started")

        while STATE.get('system.running'):
            try:
                if STATE.get('signals.analysis_ready'):
                    if (STATE.get('signals.awaiting_confirmation')
                            or STATE.get('signals.confirmation_sent')):
                        time.sleep(10)
                        continue
                    signal = STATE.get('signals.analysis')

                    if signal and not STATE.get('position.open'):
                        decision = self.approve(signal)
                        from src.trade_analytics import log_funnel
                        if decision['approved']:
                            log_funnel('risk_ok', signal)
                        else:
                            log_funnel('risk_block', signal, decision.get('reason', ''))
                        needs_confirm = self._needs_confirmation()
                        approved = decision['approved']
                        STATE.update('signals', {
                            'analysis_ready':        False,
                            'risk_approved':         approved,
                            'risk':                  decision,
                            'execute_now':           approved and not needs_confirm,
                            'awaiting_confirmation': approved and needs_confirm,
                            'confirmation_sent':     False,
                        })

            except Exception as e:
                STATE.add_error(f"Risk Agent: {str(e)[:60]}")

            time.sleep(10)

        STATE.set_agent_status('risk', 'STOPPED')


# ══════════════════════════════════════════════════════════════════
# EXECUTION AGENT
# ══════════════════════════════════════════════════════════════════

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
        if widen and not dyn:
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
                from agents.data_agent import DataAgent
                token = DataAgent().get_groww_token()
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

        liq = check_liquidity(
            params.get('strike', 0),
            params.get('opt_type', 'CE'),
            params.get('expiry', ''),
            params.get('premium', 0),
        )
        if not liq['ok']:
            return {'ok': False, 'reason': liq['reason']}

        return {'ok': True, 'reason': 'All pre-trade checks passed ✅'}

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
            return result
        except Exception as e:
            error_str = str(e).lower()
            # If token expired, refresh and retry once
            if 'auth' in error_str or 'expired' in error_str or 'invalid' in error_str:
                print(f"🔄 Token expired during order: {str(e)[:40]}")
                # Refresh token
                try:
                    from agents.data_agent import DataAgent
                    data = DataAgent()
                    fresh_token = data.get_groww_token()
                    STATE.set('system.groww_token', fresh_token)
                    
                    # Retry with fresh token
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
                        print(f"✅ Order placed after token refresh")
                        return result
                except:
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

class MonitorAgent(threading.Thread):

    def __init__(self, messenger: Messenger):
        super().__init__(daemon=True, name='MonitorAgent')
        self.messenger    = messenger
        self.last_hourly  = -1

    def _live_sell(self, position: dict, qty: int, reason: str) -> dict:
        """Execute Groww market sell (live mode only)."""
        if os.getenv('PAPER_MODE', 'true').lower() == 'true':
            return {'success': True, 'paper': True}
        token = STATE.get('system.groww_token', '') or os.getenv('GROWW_ACCESS_TOKEN', '')
        if not token:
            return {'success': False, 'error': 'No Groww token'}
        from src.groww_trader import GrowwTrader
        from src.safety import verify_order_filled
        from src.groww_symbols import groww_option_symbol

        trader = GrowwTrader(token)
        cid = position.get('contract_id')
        if not cid:
            zone = STATE.get('zone', {})
            cid = groww_option_symbol(
                'BANKNIFTY',
                position.get('strike') or zone.get('strike', 0),
                position.get('opt_type') or zone.get('opt_type', 'CE'),
                position.get('expiry') or zone.get('expiry', ''),
            )
        result = trader.sell_option(cid, qty, reason)
        if result.get('success') and result.get('order_id'):
            fill = verify_order_filled(token, result['order_id'], max_wait_sec=25)
            if not fill.get('filled'):
                self.messenger.send(
                    f"⚠️ *Sell not confirmed*\n{fill.get('reason', '')}\n"
                    f"Check Groww app immediately."
                )
                return {'success': False, 'error': fill.get('reason')}
        elif not result.get('success'):
            self.messenger.send(
                f"❌ *Groww sell failed*\n{result.get('error', 'Unknown')}\n"
                f"_Close manually on Groww if needed_"
            )
        return result

    def check_position(self):
        if not STATE.get('position.open'):
            return

        current      = STATE.get('market.price', 0)
        position     = STATE.get('position')
        entry        = position.get('entry_price', 0)
        trail_sl     = position.get('trail_sl', entry * 0.70)
        peak         = position.get('peak_premium', entry)
        tgt_prem     = position.get('tgt_prem', entry * 2)
        leg1_done    = position.get('leg1_done', False)
        learning_id  = position.get('learning_id', 0)
        bnf_entry    = position.get('bnf_at_entry', current)
        qty          = position.get('qty', 15)
        leg1_units   = qty // 2
        leg2_units   = qty - leg1_units

        from src.premium_feed import get_position_premium
        est_prem = get_position_premium(position, current)

        # Update peak
        new_peak = max(peak, est_prem)
        if new_peak > peak:
            STATE.set('position.peak_premium', new_peak)

        # Update trailing SL (80% of peak, minimum = SL prem)
        sl_from_trail   = round(new_peak * 0.80, 0)
        sl_from_initial = position.get('sl_prem', entry * 0.70)
        new_trail_sl    = max(sl_from_trail, sl_from_initial, trail_sl)

        if new_trail_sl > trail_sl + 5:
            STATE.set('position.trail_sl', new_trail_sl)

        # ── MAE / MFE tracking ────────────────────────────────────
        rem = leg2_units if leg1_done else qty
        unrealized = round((est_prem - entry) * rem, 0)
        if leg1_done:
            unrealized += position.get('leg1_profit', 0)
        from src.trade_analytics import update_mae_mfe
        update_mae_mfe(learning_id, unrealized)
        cur_mae = position.get('mae_rs', 0)
        cur_mfe = position.get('mfe_rs', 0)
        new_mae = min(cur_mae, unrealized) if cur_mae else min(0, unrealized)
        new_mfe = max(cur_mfe, unrealized) if cur_mfe else max(0, unrealized)
        STATE.update('position', {'mae_rs': new_mae, 'mfe_rs': new_mfe})

        # ── Check exits ───────────────────────────────────────────
        exit_now    = False
        exit_reason = ''
        pnl_rs      = 0

        # EOD exit
        if datetime.now(IST).time() >= dtime(15, 10):
            exit_now    = True
            exit_reason = f"⏰ EOD exit at ₹{est_prem:.0f}"
            pnl_rs      = round((est_prem - entry) * qty, 0)

        # Leg 1 exit (50% at 1.5x)
        elif not leg1_done and est_prem >= entry * 1.5:
            leg1_profit = round((est_prem - entry) * leg1_units, 0)
            sell_r = self._live_sell(position, leg1_units, 'LEG1_PROFIT')
            if not sell_r.get('success') and not sell_r.get('paper'):
                return
            STATE.set('position.leg1_done', True)
            STATE.set('position.leg1_profit', leg1_profit)
            STATE.set('position.trail_sl', entry)  # Move SL to breakeven

            msg = (
                f"🎯 *Leg 1 Profit Locked!* (paper journal)\n"
                f"Exited {leg1_units} units at ₹{est_prem:.0f}\n"
                f"Profit: ₹{leg1_profit:,} secured ✅\n"
                f"Remaining {leg2_units} units: SL → breakeven ₹{entry:.0f}\n"
                f"Target: ₹{tgt_prem:.0f} — free trade now!\n"
                f"_Brain tracking leg 2 until close_ 🧠"
            )
            self.messenger.send(msg)
            return

        # Full target
        elif est_prem >= tgt_prem:
            exit_now    = True
            exit_reason = f"🎯 Full target ₹{tgt_prem:.0f} hit!"
            pnl_rs      = round((est_prem - entry) * (leg2_units if leg1_done else qty), 0)
            if leg1_done:
                pnl_rs += position.get('leg1_profit', 0)

        # Trail SL hit
        elif est_prem <= new_trail_sl and new_peak > entry * 1.2:
            exit_now    = True
            exit_reason = f"📈 Trail SL at ₹{new_trail_sl:.0f} (peak ₹{new_peak:.0f})"
            pnl_rs      = round((new_trail_sl - entry) * (leg2_units if leg1_done else qty), 0)
            if leg1_done:
                pnl_rs += position.get('leg1_profit', 0)

        # Initial SL hit
        elif est_prem <= sl_from_initial:
            exit_now    = True
            exit_reason = f"🛑 SL hit at ₹{sl_from_initial:.0f}"
            pnl_rs      = round((est_prem - entry) * qty, 0)

        # ── Execute exit ──────────────────────────────────────────
        if exit_now:
            rem_qty = leg2_units if leg1_done else qty
            sell_r = self._live_sell(position, rem_qty, exit_reason)
            if not sell_r.get('success') and not sell_r.get('paper'):
                return

            emoji   = '🟢' if pnl_rs >= 0 else '🔴'
            pnl_pct = round(pnl_rs / (entry * qty) * 100, 1) if entry and qty else 0

            # Track consecutive losses for circuit breaker
            now_ist  = datetime.now(IST)
            week_key = now_ist.strftime('%Y-W%W')

            # Reset weekly counter on new week
            if STATE.get('system.week_start') != week_key:
                STATE.set('system.week_start',   week_key)
                STATE.set('system.weekly_losses', 0)

            if pnl_rs < 0:
                current_losses = STATE.get('system.weekly_losses', 0)
                new_losses     = current_losses + 1
                STATE.set('system.weekly_losses', new_losses)

                if new_losses >= 2:
                    STATE.set('system.paused', True)
                    self.messenger.send(
                        "🛑 *Circuit Breaker Triggered*\n\n"
                        "2 losses this week — bot auto-paused.\n"
                        "Your capital is protected. 🛡️\n\n"
                        "Review the trades, then type /resume\n"
                        "when you're ready to continue."
                    )
            else:
                # Win resets consecutive loss streak
                STATE.set('system.weekly_losses', 0)

            mae_rs = position.get('mae_rs', 0) or BRAIN._get_field(learning_id, 'mae_rs') or 0
            mfe_rs = position.get('mfe_rs', 0) or BRAIN._get_field(learning_id, 'mfe_rs') or 0
            paper  = os.getenv('PAPER_MODE', 'true').lower() == 'true'
            slippage_rs = 0
            if paper:
                from src.trade_analytics import apply_paper_slippage
                pnl_rs, slippage_rs = apply_paper_slippage(pnl_rs, qty)
                pnl_pct = round(pnl_rs / (entry * qty) * 100, 1) if entry and qty else 0

            entry_time = position.get('entry_time', '')
            hold_min = BRAIN._hold_minutes(entry_time) if entry_time else 0
            session  = STATE.get('market.session', '')
            from src.trade_analytics import detect_theta_loss
            theta_decay = detect_theta_loss(
                hold_min, session, pnl_rs, mfe_rs, exit_reason
            )

            # Record in brain + learn
            brain_result = BRAIN.record_exit(learning_id, {
                'exit_prem': est_prem,
                'pnl_rs':    pnl_rs,
                'pnl_pct':   pnl_pct,
                'reason':    exit_reason,
                'session':   session,
                'regime':    STATE.get('market.regime', ''),
                'score':     STATE.get('signals.analysis', {}).get('score', 5) if STATE.get('signals.analysis') else 5,
                'rsi':       STATE.get('market.rsi_5m', 50),
                'mae_rs':    mae_rs,
                'mfe_rs':    mfe_rs,
                'slippage_rs': slippage_rs,
                'theta_decay': theta_decay,
            })

            # Self-validation lesson
            self_lesson = ''
            try:
                bias        = position.get('opt_type', 'CE')
                entry_bnf   = position.get('bnf_at_entry', 0)
                current_bnf = STATE.get('market.price', 0)
                bnf_moved   = current_bnf - entry_bnf
                prediction_correct = (
                    (bias == 'CE' and bnf_moved > 0) or
                    (bias == 'PE' and bnf_moved < 0)
                )
                if prediction_correct and pnl_rs < 0:
                    self_lesson = "Direction correct but SL too tight — review ATR sizing"
                elif not prediction_correct and pnl_rs > 0:
                    self_lesson = "Direction wrong but exited with profit — lucky trade"
                elif prediction_correct and pnl_rs > 0:
                    self_lesson = "Direction correct + profit — valid setup ✅"
                else:
                    self_lesson = "Direction wrong + loss — review zone logic"
                if self_lesson:
                    BRAIN.add_lesson(learning_id, self_lesson)
            except Exception:
                pass

            lesson = BRAIN._get_field(learning_id, 'lesson') or brain_result.get('lesson', '')

            # Update today P&L
            today_pnl = STATE.get('brain.today_pnl', 0) + pnl_rs
            STATE.set('brain.today_pnl', today_pnl)
            week_pnl = STATE.get('system.week_pnl', 0) + pnl_rs
            STATE.set('system.week_pnl', week_pnl)

            from src.capital_guard import check_weekly_loss_cap, MAX_WEEKLY_LOSS_RS
            if week_pnl <= -MAX_WEEKLY_LOSS_RS:
                STATE.set('system.paused', True)
                self.messenger.send(
                    f"🛑 *Weekly loss cap reached* (₹{week_pnl:,.0f})\n"
                    f"Bot paused until Monday. Capital protected."
                )

            # Clear position
            from src.position_store import clear_position
            clear_position()
            STATE.update('position', {
                'open': False, 'name': '', 'entry_price': 0,
                'sl_prem': 0, 'tgt_prem': 0, 'trail_sl': 0,
                'peak_premium': 0, 'leg1_done': False,
                'leg1_profit': 0, 'learning_id': 0
            })
            STATE.set('signals.exit_now', False)

            paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
            if paper:
                from src.paper_journal import format_paper_exit
                from src.brain_metrics import compute_paper_confidence
                self.messenger.send(format_paper_exit(
                    {
                        'learning_id': learning_id,
                        'name':          position.get('name'),
                        'entry_price':   entry,
                        'exit_prem':     est_prem,
                        'pnl_pct':       pnl_pct,
                        'exit_reason':   exit_reason,
                        'mae_rs':        mae_rs,
                        'mfe_rs':        mfe_rs,
                        'slippage_rs':   slippage_rs,
                    },
                    lesson, brain_result.get('outcome', ''),
                    pnl_rs, today_pnl,
                ))
                conf = compute_paper_confidence()
                if conf['score'] < 40 and conf['stats']['total'] >= 3:
                    self.messenger.send(
                        f"⚠️ *Brain alert:* confidence {conf['score']}/100\n"
                        f"Bot will tighten filters (higher min score).\n"
                        f"_Stay paper — review /journal and /readiness_"
                    )
            else:
                self.messenger.send(
                    f"{emoji} *EXIT — LIVE*\n"
                    f"Option: {position.get('name')}\n"
                    f"Reason: {exit_reason}\n\n"
                    f"P&L: ₹{pnl_rs:,} ({pnl_pct:+.1f}%)\n"
                    f"Today total: ₹{today_pnl:,}\n"
                    f"_Groww sell executed ✅_"
                )

        else:
            # Hourly P&L update
            hour = datetime.now(IST).hour
            if hour != self.last_hourly:
                self.last_hourly = hour
                leg1_p = position.get('leg1_profit', 0)
                rem    = leg2_units if leg1_done else qty
                total_pnl = round((est_prem - entry) * rem, 0)
                if leg1_done:
                    total_pnl += leg1_p
                emoji = '📈' if total_pnl >= 0 else '📉'
                self.messenger.send(
                    f"{emoji} *Hourly Update*\n"
                    f"BNF: {current:,} | Est. Premium: ₹{est_prem:.0f}\n"
                    f"P&L so far: ₹{total_pnl:,}\n"
                    f"Trail SL: ₹{new_trail_sl:.0f} | Target: ₹{tgt_prem:.0f}"
                )

    def run(self):
        STATE.set_agent_status('monitor', 'RUNNING')
        print("👁️ Monitor Agent started")

        while STATE.get('system.running'):
            try:
                now = datetime.now(IST).time()
                # Run monitor during market hours AND until 3:20 PM
                # to ensure EOD exit fires even if market_open flips False
                market_window = dtime(9, 15) <= now <= dtime(15, 20)
                if market_window or STATE.get('position.open'):
                    self.check_position()
            except Exception as e:
                STATE.add_error(f"Monitor Agent: {str(e)[:60]}")

            time.sleep(30)  # Every 30 seconds

        STATE.set_agent_status('monitor', 'STOPPED')
