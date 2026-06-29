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

        # ── Wednesday expiry filter ───────────────────────────────
        now     = datetime.now(IST)
        weekday = now.weekday()  # 2 = Wednesday
        if weekday == 2:
            # Wednesday = expiry day. Much stricter.
            if score < 8:
                return {
                    'approved': False,
                    'reason':   f'📅 Wednesday expiry day — need score ≥ 8 (got {score}). Skipping.'
                }
            warnings.append('⚠️ Expiry day — stricter rules applied (score ≥ 8 ✅)')


        # ── Brain check ───────────────────────────────────────────
        brain        = STATE.get('brain')
        min_score    = brain.get('min_score', 5)
        max_trades   = brain.get('max_trades_day', 1)
        trades_today = brain.get('trades_today', 0)
        avoid_hours  = brain.get('avoid_hours', [])
        hour         = datetime.now(IST).hour

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
        if regime == 'TIGHT_RANGE':
            return {'approved': False, 'reason': "Market in tight range — no edge"}
        if regime == 'RANGING':
            warnings.append("⚠️ Ranging market — proceed carefully")

        # ── Global market check ───────────────────────────────────
        try:
            import yfinance as yf
            sp500 = yf.Ticker('^GSPC').history(period='3d',interval='1d').dropna()
            if len(sp500) >= 2:
                sp_move = (float(sp500['Close'].iloc[-1]) -
                           float(sp500['Close'].iloc[-2])) / \
                           float(sp500['Close'].iloc[-2]) * 100
                if trend == 'BULLISH' and sp_move <= -1.5:
                    return {'approved': False,
                            'reason': f"S&P500 fell {sp_move:.1f}% — global headwind"}
                if sp_move >= 1.0 and trend == 'BULLISH':
                    reasons.append(f"✅ S&P500 +{sp_move:.1f}% — global tailwind")
        except:
            pass

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

        return {
            'approved':   True,
            'reasons':    reasons,
            'warnings':   warnings,
            'confidence': min(50 + score * 5, 95)
        }

    def run(self):
        STATE.set_agent_status('risk', 'RUNNING')
        print("🛡️ Risk Agent started")

        while STATE.get('system.running'):
            try:
                if STATE.get('signals.analysis_ready'):
                    signal = STATE.get('signals.analysis')

                    if signal and not STATE.get('position.open'):
                        decision = self.approve(signal)
                        STATE.update('signals', {
                            'analysis_ready': False,
                            'risk_approved':  decision['approved'],
                            'risk':           decision,
                            'execute_now':    decision['approved']
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
        """Build precise entry parameters using ATR-adjusted SL/Target"""
        price   = signal.get('price', 0)
        trend   = signal.get('trend', 'BULLISH')
        atr     = STATE.get('market.atr', 500)
        zone    = STATE.get('zone')
        premium = zone.get('premium', 265) if zone else 265

        # ATR-based SL (calibrated from real BankNifty ATR ranges)
        if   atr < 486:  sl_pct, tgt_mul = 0.25, 2.5
        elif atr < 875:  sl_pct, tgt_mul = 0.30, 2.0
        elif atr < 1159: sl_pct, tgt_mul = 0.35, 2.0
        else:            sl_pct, tgt_mul = 0.40, 1.8

        sl_prem  = round(premium * (1 - sl_pct), 0)
        tgt_prem = round(premium * tgt_mul, 0)

        return {
            'name':     zone.get('option_name', f'BANKNIFTY {zone.get("strike", 0)} {zone.get("opt_type", "CE")}') if zone else '',
            'strike':   zone.get('strike', 0) if zone else 0,
            'opt_type': zone.get('opt_type', 'CE') if zone else 'CE',
            'expiry':   zone.get('expiry', '') if zone else '',
            'premium':  premium,
            'sl_prem':  sl_prem,
            'tgt_prem': tgt_prem,
            'lot_cost': premium * 15,
            'max_loss': round(premium * sl_pct * 15, 0),
            'max_gain': round(premium * (tgt_mul-1) * 15, 0),
        }

    def place_order(self, params: dict) -> dict:
        """Execute order via Groww API"""
        if self.paper:
            self.trade_counter += 1
            return {
                'success':  True,
                'order_id': f"PAPER_{self.trade_counter:04d}",
                'paper':    True
            }
        # Live Groww execution
        token = os.getenv('GROWW_ACCESS_TOKEN', '')
        try:
            from src.groww_trader import GrowwTrader
            trader = GrowwTrader()

            # Pre-flight: warn if balance check unavailable
            from src.safety import check_groww_balance
            bal = check_groww_balance(token, required_amount=4500)
            if not bal.get('sufficient'):
                return {
                    'success': False,
                    'error': f"Insufficient F&O balance: {bal.get('reason')}"
                }

            return trader.buy_option(
                'BANKNIFTY',
                params['strike'],
                params['opt_type'],
                params['expiry'],
                params['sl_prem'],
                params['tgt_prem'],
                lots=1
            )
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def run(self):
        STATE.set_agent_status('execution', 'RUNNING')
        print("⚡ Execution Agent started")

        while STATE.get('system.running'):
            try:
                if STATE.get('signals.execute_now') and not STATE.get('position.open'):
                    signal   = STATE.get('signals.analysis')
                    risk     = STATE.get('signals.risk')
                    params   = self.calculate_trade_params(signal)

                    # Clear execute signal immediately (prevent double entry)
                    STATE.set('signals.execute_now', False)

                    if not params.get('name'):
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
                        # Update position state
                        STATE.update('position', {
                            'open':         True,
                            'name':         params['name'],
                            'entry_price':  params['premium'],
                            'entry_time':   datetime.now(IST).strftime('%H:%M'),
                            'sl_prem':      params['sl_prem'],
                            'tgt_prem':     params['tgt_prem'],
                            'trail_sl':     params['sl_prem'],
                            'peak_premium': params['premium'],
                            'leg1_done':    False,
                            'leg1_profit':  0,
                            'qty':          15,
                            'learning_id':  learning_id,
                            'bnf_at_entry': signal.get('price', 0),
                        })

                        # Update brain trades today
                        trades_today = STATE.get('brain.trades_today', 0)
                        STATE.set('brain.trades_today', trades_today + 1)

                        # Mark zone used
                        STATE.set('zone.used', True)
                        STATE.set('zone.active', False)

                        # Send Telegram
                        mode = "📝 Paper" if self.paper else "💸 LIVE"
                        risk_reasons = '\n'.join(
                            f"  {r}" for r in
                            (signal.get('reasons', [])[:3] + risk.get('reasons', [])[:2])
                        )

                        msg = (
                            f"⚡ *ENTRY EXECUTED — {mode}*\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"Option: *{params['name']}*\n"
                            f"Premium: ₹{params['premium']}/unit\n"
                            f"Cost:    ₹{params['lot_cost']:,} (1 lot)\n\n"
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

        # Estimate current premium from BNF movement
        # Delta varies by moneyness — OTM options have lower delta
        bnf_move  = current - bnf_entry
        strike    = STATE.get('zone.strike', 0)
        otm_gap   = abs(strike - bnf_entry) if strike and bnf_entry else 300
        if   otm_gap > 500: delta = 0.20   # Deep OTM
        elif otm_gap > 300: delta = 0.28   # OTM
        elif otm_gap > 150: delta = 0.38   # Slightly OTM
        else:               delta = 0.50   # Near ATM
        est_prem  = round(entry + bnf_move * delta, 1)
        est_prem  = max(est_prem, 5)  # Floor at Rs 5

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

        # ── Check exits ───────────────────────────────────────────
        exit_now    = False
        exit_reason = ''
        pnl_rs      = 0

        # EOD exit
        if datetime.now(IST).time() >= dtime(15, 10):
            exit_now    = True
            exit_reason = f"⏰ EOD exit at ₹{est_prem:.0f}"
            pnl_rs      = round((est_prem - entry) * 15, 0)

        # Leg 1 exit (50% at 1.5x)
        elif not leg1_done and est_prem >= entry * 1.5:
            leg1_profit = round((est_prem - entry) * 7, 0)
            STATE.set('position.leg1_done', True)
            STATE.set('position.leg1_profit', leg1_profit)
            STATE.set('position.trail_sl', entry)  # Move SL to breakeven

            msg = (
                f"🎯 *Leg 1 Profit Locked!*\n"
                f"Exited 7 units at ₹{est_prem:.0f}\n"
                f"Profit: ₹{leg1_profit:,} secured ✅\n"
                f"Remaining 8 units: SL moved to breakeven ₹{entry:.0f}\n"
                f"Target: ₹{tgt_prem:.0f} — free trade now!"
            )
            self.messenger.send(msg)
            return

        # Full target
        elif est_prem >= tgt_prem:
            exit_now    = True
            exit_reason = f"🎯 Full target ₹{tgt_prem:.0f} hit!"
            pnl_rs      = round((est_prem - entry) * (8 if leg1_done else 15), 0)
            if leg1_done:
                pnl_rs += position.get('leg1_profit', 0)

        # Trail SL hit
        elif est_prem <= new_trail_sl and new_peak > entry * 1.2:
            exit_now    = True
            exit_reason = f"📈 Trail SL at ₹{new_trail_sl:.0f} (peak ₹{new_peak:.0f})"
            pnl_rs      = round((new_trail_sl - entry) * (8 if leg1_done else 15), 0)
            if leg1_done:
                pnl_rs += position.get('leg1_profit', 0)

        # Initial SL hit
        elif est_prem <= sl_from_initial:
            exit_now    = True
            exit_reason = f"🛑 SL hit at ₹{sl_from_initial:.0f}"
            pnl_rs      = round((est_prem - entry) * 15, 0)

        # ── Execute exit ──────────────────────────────────────────
        if exit_now:
            emoji   = '🟢' if pnl_rs >= 0 else '🔴'
            pnl_pct = round(pnl_rs / (entry * 15) * 100, 1) if entry else 0

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

            # Record in brain
            BRAIN.record_exit(learning_id, {
                'exit_prem': est_prem,
                'pnl_rs':    pnl_rs,
                'pnl_pct':   pnl_pct,
                'reason':    exit_reason,
                'session':   STATE.get('market.session', ''),
                'regime':    STATE.get('market.regime', ''),
                'score':     5,
            })

            # Self-validation: did bot's prediction match reality?
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
                    lesson = "Direction correct but SL too tight — review ATR sizing"
                elif not prediction_correct and pnl_rs > 0:
                    lesson = "Direction wrong but exited with profit — lucky trade, review entry"
                elif prediction_correct and pnl_rs > 0:
                    lesson = "Direction correct + profit — valid setup ✅"
                else:
                    lesson = "Direction wrong + loss — review zone and structure logic"

                BRAIN.add_lesson(learning_id, lesson)
                print(f"🧠 Self-validation: {lesson}")

            except Exception as e:
                pass

            # Update today P&L
            today_pnl = STATE.get('brain.today_pnl', 0) + pnl_rs
            STATE.set('brain.today_pnl', today_pnl)

            # Track weekly losses for circuit breaker
            if pnl_rs < 0:
                weekly_losses = STATE.get('system.weekly_losses', 0) + 1
                STATE.set('system.weekly_losses', weekly_losses)
            else:
                # Win resets consecutive loss count
                STATE.set('system.weekly_losses', 0)

            # Clear position
            STATE.update('position', {
                'open': False, 'name': '', 'entry_price': 0,
                'sl_prem': 0, 'tgt_prem': 0, 'trail_sl': 0,
                'peak_premium': 0, 'leg1_done': False,
                'leg1_profit': 0, 'learning_id': 0
            })
            STATE.set('signals.exit_now', False)

            self.messenger.send(
                f"{emoji} *EXIT*\n"
                f"Option: {position.get('name')}\n"
                f"Reason: {exit_reason}\n\n"
                f"P&L: ₹{pnl_rs:,} ({pnl_pct:+.1f}%)\n"
                f"Today total: ₹{today_pnl:,}"
            )

            paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
            if not paper:
                self.messenger.send(
                    "⚡ Closing position on Groww...\n"
                    "Check positions to confirm."
                )

        else:
            # Hourly P&L update
            hour = datetime.now(IST).hour
            if hour != self.last_hourly:
                self.last_hourly = hour
                leg1_p = position.get('leg1_profit', 0)
                total_pnl = round((est_prem - entry) * (8 if leg1_done else 15), 0)
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
