"""Monitor Agent — watches position every 30 seconds."""

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
        if os.getenv('PAPER_MODE', 'true').lower() != 'true' and result.get('paper'):
            self.messenger.send(
                "🛑 *Live sell blocked*\n\n"
                "Groww not connected — position may still be open.\n"
                "_Close manually on Groww app immediately._"
            )
            return {'success': False, 'error': 'Fake paper sell rejected in live mode'}
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
        # get_position_premium → smart_mark_to_market when watch mode active

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

            leg1_lbl = 'paper' if os.getenv('PAPER_MODE', 'true').lower() == 'true' else 'live'
            msg = (
                f"🎯 *Leg 1 Profit Locked!* ({leg1_lbl})\n"
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
                try:
                    from src.shadow_learning import tick_shadow_trades
                    tick_shadow_trades()
                except Exception:
                    pass
            except Exception as e:
                STATE.add_error(f"Monitor Agent: {str(e)[:60]}")

            try:
                from src.position_watch import watch_mode_active
                from src.shadow_learning import VIRTUAL_TICK_IDLE_SEC
                sleep_secs = 3 if watch_mode_active() else VIRTUAL_TICK_IDLE_SEC
            except Exception:
                sleep_secs = 10
            time.sleep(sleep_secs)

        STATE.set_agent_status('monitor', 'STOPPED')


# ══════════════════════════════════════════════════════════════════
# SIM LEARNING AGENT — autonomous virtual CE/PE on live market flow
# ══════════════════════════════════════════════════════════════════

