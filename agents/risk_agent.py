"""Risk Agent — approves or rejects every trade."""

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

        from src.wr_filters import check_session_win_rate
        sess_wr = check_session_win_rate(session)
        if not sess_wr.get('ok', True):
            return {'approved': False, 'reason': sess_wr['reason']}

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

        # ── IV rank gate (option buyers — cached NSE chain) ───────
        try:
            from src.greeks_gates import check_iv_rank_for_buyers
            iv_gate = check_iv_rank_for_buyers(score)
            if not iv_gate.get('ok', True):
                return {'approved': False, 'reason': iv_gate['reason']}
            if iv_gate.get('warning'):
                warnings.append(iv_gate['warning'])
        except Exception:
            pass

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

        # ── Training phase: sim-only weeks block paper/live entries ──
        try:
            from src.shadow_learning import paper_trading_allowed, learning_phase_info
            if not paper_trading_allowed():
                info = learning_phase_info()
                return {
                    'approved': False,
                    'reason': (
                        f"🎓 *Week 1–2: virtual sim only*\n"
                        f"Paper `/execute` unlocks in *{info['days_until_paper']}* day(s).\n"
                        f"Use `/shadow` and `/ml` — bot learns with ₹0 risk."
                    ),
                }
        except Exception:
            pass

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
            try:
                from src.shadow_tuning import shadow_score_adjustment
                sh = shadow_score_adjustment(min_score)
                if sh.get('boost', 0) > 0 and sh.get('reason'):
                    warnings.append(sh['reason'])
                else:
                    warnings.append(
                        f"🧠 Recent paper weak — min score raised to {dyn_score}"
                    )
            except Exception:
                warnings.append(
                    f"🧠 Min score raised to {dyn_score}"
                )
            min_score = dyn_score
        max_trades   = brain.get('max_trades_day', 1)
        trades_today = brain.get('trades_today', 0)
        try:
            from src.shadow_learning import training_phase, learning_phase_info
            from src.learning_scoreboard import post_learning_max_trades
            cap = post_learning_max_trades()
            max_trades = min(max_trades, cap)
            info = learning_phase_info()
            phase = training_phase()
            if phase == 'SIM':
                warnings.append(
                    f"🎓 Week 1–2 sim ({info['days_until_paper']}d to paper) — "
                    f"virtual drills only, no /execute"
                )
            elif phase == 'PAPER':
                warnings.append(
                    f"📝 Week 3–4 paper ({info['days_left']}d to live window) — "
                    f"max {cap} confirmed trade/day"
                )
            else:
                warnings.append(
                    f"🎯 Month complete — max {cap} trade/day; `/readiness` before live"
                )
        except Exception:
            pass
        avoid_hours  = brain.get('avoid_hours', [])
        hour         = datetime.now(IST).hour

        combo = check_pattern_combo(session, hour, score, regime)
        if combo['block']:
            return {'approved': False, 'reason': combo['reason']}

        from src.wr_filters import check_expiry_week_rules
        exp_chk = check_expiry_week_rules(score)
        if not exp_chk.get('ok', True):
            return {'approved': False, 'reason': exp_chk['reason']}
        min_score += exp_chk.get('min_score_boost', 0)

        if score < min_score:
            return {'approved': False,
                    'reason': f"Score {score} < brain min {min_score}"}

        if trades_today >= max_trades:
            try:
                from src.loss_recovery import check_recovery_trade_allowed
                rec = check_recovery_trade_allowed(signal, trades_today, max_trades)
                if rec.get('allowed'):
                    warnings.append(rec.get('note', 'Recovery trade slot'))
                else:
                    return {'approved': False,
                            'reason': rec.get('reason', f"Max trades/day ({max_trades})")}
            except Exception:
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
            if BLOCK_ON_FILTER_ERROR:
                return {
                    'approved': False,
                    'reason': f'🛑 Trade filters failed — {str(e)[:50]}',
                }
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
            if BLOCK_ON_FILTER_ERROR:
                return {
                    'approved': False,
                    'reason': f'🛑 Market validator failed — {str(e)[:50]}',
                }
            warnings.append(f"⚠️ Validator skipped: {str(e)[:40]}")

        # ── Chart S/R + unified F&O flow (OI walls, theta, swing lines) ──
        try:
            from src.market_flow import flow_allows_trade
            from src.chart_levels import check_chart_levels
            from src.wr_filters import (
                check_min_flow_score, check_oi_wall_veto,
                check_shadow_agreement, check_premium_sweet_spot,
            )
            from src.trend_strength import check_trend_strength
            from src.max_pain_filter import check_max_pain_pin

            ts_chk = check_trend_strength(
                STATE.get('market.candles_15m', []), regime, trend
            )
            if not ts_chk.get('ok', True):
                return {'approved': False, 'reason': ts_chk['reason']}
            if ts_chk.get('reason'):
                reasons.append(ts_chk['reason'])

            mp_chk = check_max_pain_pin(trend, price)
            if not mp_chk.get('ok', True):
                return {'approved': False, 'reason': mp_chk['reason']}
            if mp_chk.get('reason'):
                reasons.append(mp_chk['reason'])

            flow_chk = flow_allows_trade(trend, price)
            flow = flow_chk.get('flow', {})
            STATE.set('signals.market_flow', flow)
            if not flow_chk.get('ok', True):
                return {'approved': False, 'reason': flow_chk['reason']}

            fs_chk = check_min_flow_score(flow, score)
            if not fs_chk.get('ok', True):
                return {'approved': False, 'reason': fs_chk['reason']}
            reasons.append(fs_chk.get('reason', ''))

            target_px = zone.get('high', price) if trend == 'BULLISH' else zone.get('low', price)
            oi_wall = check_oi_wall_veto(trend, price, target_px)
            if not oi_wall.get('ok', True):
                return {'approved': False, 'reason': oi_wall['reason']}
            if oi_wall.get('reason'):
                reasons.append(oi_wall['reason'])

            shadow_chk = check_shadow_agreement(trend)
            if not shadow_chk.get('ok', True):
                return {'approved': False, 'reason': shadow_chk['reason']}
            if shadow_chk.get('reason'):
                reasons.append(shadow_chk['reason'])

            sweet = check_premium_sweet_spot(est_prem)
            if not sweet.get('ok', True):
                return {'approved': False, 'reason': sweet['reason']}
            if sweet.get('reason'):
                reasons.append(sweet['reason'])

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
            if BLOCK_ON_FILTER_ERROR:
                return {
                    'approved': False,
                    'reason': f'🛑 Flow/OI check failed — {str(e)[:50]}',
                }
            warnings.append(f"⚠️ Flow check skipped: {str(e)[:40]}")

        return {
            'approved':   True,
            'reasons':    reasons,
            'warnings':   warnings,
            'confidence': min(50 + score * 5, 95)
        }

    def _needs_confirmation(self) -> bool:
        try:
            from src.shadow_learning import should_auto_paper_execute
            if should_auto_paper_execute():
                return False
        except Exception:
            pass
        confirm = os.getenv('CONFIRM_BEFORE_TRADE', 'true').lower()
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
                            try:
                                from src.funnel_why import set_last_block
                                set_last_block('risk_block', decision.get('reason', ''))
                            except Exception:
                                pass
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
