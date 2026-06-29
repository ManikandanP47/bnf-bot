"""
BankNifty Multi-Agent Trading System — Main Orchestrator
Starts 6 agents. Runs on Railway.app or local Mac.

Architecture (from research):
  Data     → Dhan WebSocket real-time ticks
  Analysis → SMC on live candles
  Risk     → All filters + brain check
  Execute  → Groww Smart Orders (OCO)
  Monitor  → Trailing SL + partial exit every 30s
  Learning → SQLite brain, fractional-Kelly sizing

Decisions locked in this build:
  ✅ Dhan IDX_I/"25" for BankNifty real-time data
  ✅ yfinance fallback for paper/testing
  ✅ Groww OCO for server-side SL+Target
  ✅ SQLite WAL brain (persistent across restarts)
  ✅ Min 30 trades before threshold changes
  ✅ Fractional Kelly (0.25x) position sizing
  ✅ No cron-job.org needed (internal scheduler)
  ✅ All safety edge cases handled
  ✅ PAPER_MODE=true until verified
"""

import os, sys, time, threading
from datetime import datetime
import pytz
from dotenv import load_dotenv
load_dotenv()

# All agents
from core.shared_state      import STATE
from core.messenger         import Messenger
from core.command_listener  import CommandListener
from agents.data_agent      import DataAgent
from agents.analysis_agent  import AnalysisAgent
from agents.agents          import RiskAgent, ExecutionAgent, MonitorAgent
from agents.learning_agent  import LearningAgent, BRAIN

# All safety modules
from src.safety             import check_trading_day, check_circuit_breaker
from src.scanner            import analyse
from src.zone_manager       import save_zone
from src.premarket          import run_premarket_scan, format_premarket_telegram

IST = pytz.timezone('Asia/Kolkata')


def reconcile_on_startup(messenger: Messenger):
    """
    Critical: on every startup, reconcile bot state vs
    actual Groww positions to handle Railway redeploys.
    """
    from src.safety import reconcile_positions
    token  = os.getenv('GROWW_ACCESS_TOKEN', '')
    result = reconcile_positions(token)
    alert  = result.get('alert')
    if alert:
        messenger.send(f"⚠️ *Startup Reconciliation*\n{alert}")
        print(f"  ⚠️ {alert}")
    else:
        print(f"  ✅ Reconciliation: {result.get('status', 'OK')}")


def scheduler(messenger: Messenger):
    """
    Internal scheduler — no cron-job.org needed.
    Runs all time-based tasks from inside the process.
    """
    last_premarket = -1
    last_evening   = -1
    last_weekly    = -1
    last_day_reset = -1

    while STATE.get('system.running'):
        try:
            now     = datetime.now(IST)
            hour    = now.hour
            minute  = now.minute
            weekday = now.weekday()  # 0=Mon, 6=Sun

            # ── Skip weekends (except Sunday evening tasks) ────────
            if weekday == 5:  # Saturday — full skip
                time.sleep(60)
                continue
            if weekday == 6 and hour < 20:  # Sunday — skip until 8 PM
                time.sleep(60)
                continue

            # ── Consecutive loss circuit breaker ──────────────────
            weekly_losses = STATE.get('system.weekly_losses', 0)
            if weekly_losses >= 2 and not STATE.get('system.paused'):
                STATE.set('system.paused', True)
                msg.send(
                    f"🛑 *Circuit Breaker Triggered*\n\n"
                    f"2 consecutive losses this week.\n"
                    f"Bot paused to protect your capital ₹\n\n"
                    f"Review trades, then type /resume to continue."
                )

            # ── Wednesday expiry stricter filter ──────────────────
            if weekday == 2:  # Wednesday
                current_min_score = STATE.get('brain.min_score', 5)
                if current_min_score < 8:
                    STATE.set('brain.min_score', 8)
                    print("📅 Wednesday: min score raised to 8")
            else:
                # Reset to normal on other days
                if STATE.get('brain.min_score', 5) == 8:
                    STATE.set('brain.min_score', 5)

            # ── Reset daily counters at midnight ──────────────────
            if hour == 0 and last_day_reset != now.day:
                last_day_reset = now.day
                STATE.set('brain.trades_today', 0)
                STATE.set('brain.today_pnl',    0.0)
                # Reset weekly losses every Monday
                if weekday == 0:
                    STATE.set('system.weekly_losses', 0)
                    STATE.set('system.paused', False)
                    print("📅 Monday reset: weekly losses cleared, bot resumed")

            # ── Pre-market brief 9:00 AM ──────────────────────────
            if hour == 9 and minute < 5 and last_premarket != now.day:
                last_premarket = now.day
                print("🌅 Pre-market brief...")

                day_check = check_trading_day()
                if not day_check['trade']:
                    STATE.set('system.market_open', False)
                    messenger.send(
                        f"🚫 *{day_check['reason']}*\n"
                        f"Bot staying quiet today."
                    )
                else:
                    brief = run_premarket_scan()
                    messenger.send(format_premarket_telegram(brief))
                    if not brief.get('tradeable', True):
                        STATE.set('system.market_open', False)
                        messenger.send("⚠️ High VIX — pausing entries today")

            # ── Circuit breaker check (every 30 min market hours) ─
            if 9 <= hour <= 15 and minute % 30 < 2:
                cb = check_circuit_breaker()
                if cb.get('halted'):
                    messenger.send(cb['reason'])
                    STATE.set('system.market_open', False)

            # ── Evening scan 8:15 PM ──────────────────────────────
            if hour == 20 and 13 <= minute <= 23 and last_evening != now.day:
                last_evening = now.day
                print("🌙 Evening scan...")

                result = analyse()
                if result.get('setup'):
                    zone = save_zone(result)
                    if zone:
                        STATE.update('zone', {
                            'active':      True,
                            'low':         zone.get('zone_low', 0),
                            'high':        zone.get('zone_high', 0),
                            'bias':        result.get('trend'),
                            'score':       result.get('score', 0),
                            'option_name': result.get('name', ''),
                            'strike':      result.get('strike', 0),
                            'opt_type':    result.get('opt_type', 'CE'),
                            'expiry':      result.get('expiry', ''),
                            'premium':     result.get('premium', 265),
                            'sl_prem':     result.get('sl_prem', 186),
                            'tgt_prem':    result.get('tgt_prem', 530),
                            'saved_at':    now.strftime('%H:%M'),
                            'used':        False,
                        })

                        bias_e  = '🟢' if result.get('trend')=='BULLISH' else '🔴'
                        stars   = '⭐' * min(result.get('score',0), 5)
                        reasons = '\n'.join(
                            f"  {r}" for r in result.get('reasons',[])[:4]
                        )
                        messenger.send(
                            f"🌙 *Evening Scan*\n"
                            f"━━━━━━━━━━━━━━━━━\n"
                            f"{bias_e} {result.get('trend')} | {stars}\n\n"
                            f"📌 *Zone saved:*\n"
                            f"  {zone.get('zone_low'):,.0f}–"
                            f"{zone.get('zone_high'):,.0f}\n"
                            f"  Option: *{result.get('name')}*\n"
                            f"  Premium: ~₹{result.get('premium')}/unit\n\n"
                            f"📋 Why:\n{reasons}\n\n"
                            f"_Bot watches for pullback tomorrow_ 🤖"
                        )
                else:
                    STATE.set('zone.active', False)
                    messenger.send(
                        "🌙 *Evening Scan*\n\n"
                        "No setup tomorrow. Staying quiet. ✅"
                    )

            # ── Weekly report Sunday 8 PM ─────────────────────────
            if (weekday == 6 and hour == 20 and minute < 5
                    and last_weekly != now.day):
                last_weekly = now.day
                report = BRAIN.weekly_report()
                messenger.send(report)

        except Exception as e:
            print(f"Scheduler error: {e}")
            STATE.add_error(f"Scheduler: {str(e)[:60]}")

        time.sleep(60)


def main():
    paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'

    print("="*55)
    print("🏦 BANKNIFTY MULTI-AGENT TRADING SYSTEM")
    print(f"   Mode: {'📝 PAPER' if paper else '💸 LIVE'}")
    print(f"   Time: {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
    print("="*55)

    msg = Messenger()

    # Startup reconciliation
    print("\n🔍 Startup checks...")
    reconcile_on_startup(msg)

    # Start all agents + command listener
    print("\n🚀 Starting agents...")
    agents = [
        DataAgent(),
        AnalysisAgent(),
        RiskAgent(msg),
        ExecutionAgent(msg),
        MonitorAgent(msg),
        LearningAgent(msg),
        CommandListener(msg),   # Telegram /pause /resume /status /pnl /zone
    ]

    for agent in agents:
        agent.start()
        print(f"  ✅ {agent.name}")
        time.sleep(0.5)

    # Telegram startup message
    msg.send(
        f"🚀 *Multi-Agent Bot Started*\n\n"
        f"Mode: {'📝 Paper' if paper else '💸 Live'}\n"
        f"Agents: All 7 running ✅\n"
        f"Pre-market: 9:00 AM IST\n"
        f"Evening scan: 8:15 PM IST\n"
        f"Brain: {BRAIN.get_adaptive_thresholds().get('learning_stage', '...')}\n\n"
        f"📱 *Your Commands*\n"
        f"/pause → Stop entries\n"
        f"/resume → Resume\n"
        f"/status → Bot health\n"
        f"/pnl → Today P&L\n"
        f"/zone → Saved zone\n\n"
        f"_You are always in control_ 🎯"
    )

    print("\n✅ All agents running")
    print("📱 Telegram notified")
    print("Press Ctrl+C to stop\n")

    # Scheduler runs in main thread
    try:
        scheduler(msg)
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        STATE.set('system.running', False)
        msg.send("🛑 Bot stopped manually.")


if __name__ == '__main__':
    main()
