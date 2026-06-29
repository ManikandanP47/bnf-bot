"""
BankNifty Multi-Agent Trading System — Main Orchestrator

Groww-only stack (data + execution):
  Data     → Groww LTP + Groww historical candles (cold start)
  Analysis → SMC 3-timeframe on live candles
  Risk     → Filters + brain + capital guards
  Execute  → Groww market buy + OCO (SL/target)
  Monitor  → Paper premium or live Groww sell on exit
  Learning → SQLite brain + daily P&L ledger

  PAPER_MODE=true until /readiness gates pass
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
from src.zone_manager       import save_zone, load_zone, apply_zone_to_state
from src.premarket          import run_premarket_scan, format_premarket_telegram
from src.capital_guard      import format_morning_brief
from src.paper_journal      import format_daily_paper_report

IST = pytz.timezone('Asia/Kolkata')


def reconcile_on_startup(messenger: Messenger):
    """Restore position from disk + verify against Groww."""
    from src.position_store import load_position, save_position, clear_position

    paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
    saved = load_position()
    token = STATE.get('system.groww_token', '')
    if not token:
        try:
            from src.groww_auth import fetch_groww_token
            token = fetch_groww_token()
        except Exception:
            token = os.getenv('GROWW_ACCESS_TOKEN', '')

    if paper:
        if saved.get('open'):
            STATE.update('position', saved)
            messenger.send(
                f"📂 *Position restored (paper)*\n"
                f"{saved.get('name', '')} @ ₹{saved.get('entry_price', 0)}\n"
                f"_Monitor resumed tracking_"
            )
        print("  ✅ Reconciliation: PAPER")
        return

    from src.groww_trader import GrowwTrader
    trader = GrowwTrader(token)
    groww_open = []
    try:
        for p in trader.get_positions():
            qty = int(p.get('quantity', p.get('net_qty', 0)) or 0)
            if qty != 0:
                groww_open.append(p)
    except Exception:
        groww_open = []

    if saved.get('open') and groww_open:
        STATE.update('position', saved)
        messenger.send(
            f"📂 *Position restored*\n"
            f"{saved.get('name')} — matches Groww ✅\n"
            f"_Monitor watching SL/Target_"
        )
        print("  ✅ Reconciliation: SYNC")
    elif saved.get('open') and not groww_open:
        clear_position()
        STATE.update('position', {'open': False})
        messenger.send(
            "⚠️ *Stale position cleared*\n"
            "Disk had open trade but Groww is flat — state reset."
        )
        print("  ✅ Reconciliation: CLEARED_STALE")
    elif not saved.get('open') and groww_open:
        p = groww_open[0]
        sym = p.get('trading_symbol', p.get('tradingSymbol', 'UNKNOWN'))
        qty = abs(int(p.get('quantity', p.get('net_qty', 15)) or 15))
        entry = abs(float(p.get('average_price', p.get('averagePrice', 0)) or 0))
        recovered = {
            'open': True, 'name': sym, 'entry_price': entry,
            'qty': qty, 'recovered': True,
            'contract_id': sym,
        }
        STATE.update('position', recovered)
        save_position(recovered)
        messenger.send(
            f"⚠️ *Recovered Groww position*\n"
            f"{sym} qty {qty} @ ₹{entry:.0f}\n"
            f"_Bot was not tracking — monitor active now_"
        )
        print("  ✅ Reconciliation: RECOVERED")
    else:
        print("  ✅ Reconciliation: NO_POSITION")


def load_zone_on_startup(messenger: Messenger):
    """Restore tonight's plan after restart — critical for intraday entries."""
    zone = load_zone()
    apply_zone_to_state(zone)
    if zone and not zone.get('used'):
        messenger.send(
            f"📌 *Zone restored from disk*\n"
            f"{zone.get('bias')} | "
            f"{zone.get('zone_low', 0):,.0f}–{zone.get('zone_high', 0):,.0f}\n"
            f"Option: *{zone.get('name', '')}*\n"
            f"_Watching for pullback_ 👀"
        )


def send_morning_brief_if_due(messenger: Messenger, last_sent: int) -> int:
    """9:20 AM brief, or on startup if bot missed it."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return last_sent
    due = (now.hour == 9 and 19 <= now.minute <= 25)
    missed = (now.hour == 9 and now.minute >= 26 and last_sent != now.day)
    if (due or missed) and last_sent != now.day:
        messenger.send(format_morning_brief())
        return now.day
    return last_sent


def send_morning_flow_if_due(messenger: Messenger, last_sent: int) -> int:
    """9:25 AM F&O flow dashboard — same as /flow, pushed automatically."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return last_sent
    from src.safety import check_trading_day
    if not check_trading_day().get('trade'):
        return last_sent
    due = (now.hour == 9 and 24 <= now.minute <= 29)
    missed = (
        last_sent != now.day
        and ((now.hour == 9 and now.minute >= 30) or (now.hour == 10 and now.minute < 15))
    )
    if (due or missed) and last_sent != now.day:
        from src.market_flow import format_morning_flow_telegram
        messenger.send(format_morning_flow_telegram())
        return now.day
    return last_sent


def scheduler(messenger: Messenger):
    last_premarket = -1
    last_evening   = -1
    last_weekly    = -1
    last_day_reset = -1
    last_morning   = -1
    last_morning_flow = -1
    last_daily     = -1
    last_skip      = -1
    last_readiness = -1
    last_pulse_hour = -1
    last_backup     = -1
    last_uptime_day = -1
    last_groww_alert = -1

    while STATE.get('system.running'):
        try:
            now     = datetime.now(IST)
            hour    = now.hour
            minute  = now.minute
            weekday = now.weekday()

            if weekday == 5:
                time.sleep(60)
                continue
            if weekday == 6 and hour < 20:
                time.sleep(60)
                continue

            weekly_losses = STATE.get('system.weekly_losses', 0)
            if weekly_losses >= 2 and not STATE.get('system.paused'):
                STATE.set('system.paused', True)
                messenger.send(
                    f"🛑 *Circuit Breaker Triggered*\n\n"
                    f"2 consecutive losses this week.\n"
                    f"Bot paused to protect your capital ₹\n\n"
                    f"Review trades, then type /resume to continue."
                )

            if weekday == 2:
                current_min_score = STATE.get('brain.min_score', 5)
                if current_min_score < 8:
                    STATE.set('brain.min_score', 8)
            else:
                if STATE.get('brain.min_score', 5) == 8:
                    STATE.set('brain.min_score', 5)

            if hour == 0 and last_day_reset != now.day:
                last_day_reset = now.day
                STATE.set('brain.trades_today', 0)
                STATE.set('brain.today_pnl',    0.0)
                if weekday == 0:
                    STATE.set('system.weekly_losses', 0)
                    STATE.set('system.week_pnl',      0.0)
                    STATE.set('system.paused', False)

            # 9:20 AM — always know bot status + zone
            last_morning = send_morning_brief_if_due(messenger, last_morning)

            # 9:25 AM — auto F&O flow (OI, VIX, EMA, chart lines)
            last_morning_flow = send_morning_flow_if_due(messenger, last_morning_flow)

            # Wide window 9:00-9:14 AM -- handles restarts gracefully
            if hour == 9 and minute < 15 and last_premarket != now.day:
                last_premarket = now.day
                print("🌅 Pre-market brief...")
                day_check = check_trading_day()
                if not day_check['trade']:
                    STATE.set('system.market_open', False)
                    messenger.send(f"🚫 *{day_check['reason']}*\nBot staying quiet today.")
                else:
                    brief = run_premarket_scan()
                    messenger.send(format_premarket_telegram(brief))
                    if not brief.get('tradeable', True):
                        STATE.set('system.market_open', False)
                        messenger.send("⚠️ High VIX - pausing entries today")

            if 9 <= hour <= 15 and minute % 30 < 2:
                cb = check_circuit_breaker()
                if cb.get('halted'):
                    messenger.send(cb['reason'])
                    STATE.set('system.market_open', False)

            # 3:35 PM — daily paper journal (profit/loss + brain learning)
            if hour == 15 and 34 <= minute <= 38 and last_daily != now.day:
                last_daily = now.day
                messenger.send(format_daily_paper_report())

            if hour == 15 and 38 <= minute <= 42 and last_skip != now.day:
                last_skip = now.day
                from src.trade_analytics import resolve_skipped_setups
                n = resolve_skipped_setups()
                if n:
                    messenger.send(
                        f"📚 *Skip learning updated* — {n} skipped setup(s) resolved at EOD.\n"
                        f"Type /funnel to see if your skips were good."
                    )

            # 3:40 PM — daily DB + zone backup
            if hour == 15 and 39 <= minute <= 43 and last_backup != now.day:
                last_backup = now.day
                try:
                    from src.ops_backup import run_daily_backup, format_backup_status
                    bk = run_daily_backup()
                    if bk.get('ok'):
                        messenger.send(
                            f"💾 *Daily backup OK*\n"
                            f"  {', '.join(bk.get('files', []))}\n"
                            f"  {format_backup_status()}"
                        )
                except Exception as e:
                    STATE.add_error(f"Backup: {str(e)[:40]}")

            # Uptime watchdog — alert if heartbeat stale during market hours
            if 9 <= hour <= 15 and minute in (0, 15, 30, 45):
                try:
                    from src.ops_backup import check_uptime_and_alert
                    last_uptime_day = check_uptime_and_alert(messenger, last_uptime_day)
                except Exception:
                    pass
                try:
                    from src.groww_health import maybe_alert_auth_degraded
                    last_groww_alert = maybe_alert_auth_degraded(messenger, last_groww_alert)
                except Exception:
                    pass

            # Market pulse — regular "bot is alive" check-ins (10 AM, 12 PM, 2 PM)
            try:
                from src.market_pulse import PULSE_ENABLED, should_send_pulse, format_market_pulse
                from src.safety import check_trading_day
                if (PULSE_ENABLED and 9 <= hour <= 15
                        and check_trading_day().get('trade')
                        and should_send_pulse(hour, minute, last_pulse_hour)):
                    messenger.send(format_market_pulse())
                    last_pulse_hour = hour
            except Exception:
                pass

            from src.safety import update_heartbeat
            if 9 <= hour <= 16:
                update_heartbeat()

            # Refresh /status readiness line (heavy DB work — not on every command)
            if minute == 10 and last_readiness != hour:
                last_readiness = hour
                try:
                    from src.brain_metrics import assess_live_readiness
                    r = assess_live_readiness()
                    STATE.set('system.live_readiness_summary', r['reason'])
                except Exception:
                    pass

            if hour == 20 and 13 <= minute <= 23 and last_evening != now.day:
                last_evening = now.day
                print("🌙 Evening scan...")
                result = analyse()
                if result.get('setup'):
                    zone = save_zone(result)
                    if zone:
                        STATE.update('zone', {
                            'active': True,
                            'low': zone.get('zone_low', 0),
                            'high': zone.get('zone_high', 0),
                            'bias': result.get('trend'),
                            'score': result.get('score', 0),
                            'option_name': result.get('name', ''),
                            'strike': result.get('strike', 0),
                            'opt_type': result.get('opt_type', 'CE'),
                            'expiry': result.get('expiry', ''),
                            'premium': result.get('premium', 265),
                            'sl_prem': result.get('sl_prem', 186),
                            'tgt_prem': result.get('tgt_prem', 530),
                            'saved_at': now.strftime('%H:%M'),
                            'used': False,
                        })
                        bias_e = '🟢' if result.get('trend') == 'BULLISH' else '🔴'
                        stars = '⭐' * min(result.get('score', 0), 5)
                        reasons = '\n'.join(
                            f"  {r}" for r in result.get('reasons', [])[:4]
                        )
                        messenger.send(
                            f"🌙 *Evening Scan*\n━━━━━━━━━━━━━━━━━\n"
                            f"{bias_e} {result.get('trend')} | {stars}\n\n"
                            f"📌 *Zone saved:*\n"
                            f"  {zone.get('zone_low'):,.0f}–{zone.get('zone_high'):,.0f}\n"
                            f"  Option: *{result.get('name')}*\n"
                            f"  Premium: ~₹{result.get('premium')}/unit\n\n"
                            f"📋 Why:\n{reasons}\n\n"
                            f"_Bot watches for pullback tomorrow_ 🤖"
                        )
                else:
                    STATE.set('zone.active', False)
                    messenger.send("🌙 *Evening Scan*\n\nNo setup tomorrow. Staying quiet. ✅")

            if (weekday == 6 and hour == 20 and minute < 5 and last_weekly != now.day):
                last_weekly = now.day
                messenger.send(BRAIN.weekly_report())
                try:
                    from src.history_backtest import refresh_backtest_summary, format_backtest_report
                    from src.market_context import refresh_market_context
                    from src.groww_auth import fetch_groww_token
                    tok = STATE.get('system.groww_token', '') or fetch_groww_token()
                    refresh_market_context(tok)
                    refresh_backtest_summary(tok)
                    messenger.send(format_backtest_report())
                except Exception:
                    pass

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
    if not paper:
        from src.brain_metrics import assess_live_readiness
        ready = assess_live_readiness()
        if not ready['ready']:
            print(f"⚠️ LIVE mode but not ready: {ready['reason']}")
            msg.send(
                f"⚠️ *Live mode ON but gates not passed*\n\n"
                f"{ready['reason']}\n\n"
                f"Orders will be *blocked* until paper proves edge.\n"
                f"Set `PAPER_MODE=true` or complete paper period.\n"
                f"Type /readiness for checklist."
            )
    print("\n🔍 Startup checks...")
    reconcile_on_startup(msg)
    load_zone_on_startup(msg)
    print("\n🚀 Starting agents...")
    agents = [
        DataAgent(),
        AnalysisAgent(),
        RiskAgent(msg),
        ExecutionAgent(msg),
        MonitorAgent(msg),
        LearningAgent(msg),
        CommandListener(msg),
    ]
    for agent in agents:
        agent.start()
        print(f"  ✅ {agent.name}")
        time.sleep(0.5)
    msg.send(
        f"🚀 *Multi-Agent Bot Started*\n\n"
        f"Mode: {'📝 Paper (confirm each trade)' if paper else '💸 Live'}\n"
        f"Agents: All 7 running ✅\n"
        f"Pre-market: 9:00 AM IST\n"
        f"Morning flow: 9:25 AM IST (auto /flow)\n"
        f"Evening scan: 8:15 PM IST\n\n"
        f"✑ *Your Commands*\n"
        f"/pause /resume /stop — Control bot\n"
        f"/execute /skip — Confirm or skip trade\n"
        f"/status /pnl /zone — Health & P&L\n"
        f"/journal /readiness /funnel — Paper & gates\n"
        f"/context /cpr /flow /today — Levels, F&O, AI dashboard\n"
        f"/shadow /learn /backtest — Drills, RAG memory, history\n"
        f"/help — Full command list\n\n"
        f"💓 Auto pulses: 10 AM, 12 PM, 2 PM | 🎓 Shadow drills on setups\n"
        f"💾 Daily backup 3:40 PM | 🚨 Uptime alert if bot goes silent\n"
        f"📊 Nifty correlation filter + shadow WR auto-tunes min score\n"
        f"🎯 WR filters: flow≥4, VWAP, OI walls, ADX, max pain, sweet premium\n"
        f"🔌 /groww /why — API health & why no trade\n"
        f"🤖 AI coach on /today, journal, weekly funnel & trade cards\n"
        f"_Paper first — bot must pass all gates before live ₹5k_ 🛡️"
    )
    print("\n✅ All agents running")

    def _warm_readiness_cache():
        time.sleep(5)
        try:
            from src.brain_metrics import assess_live_readiness
            r = assess_live_readiness()
            STATE.set('system.live_readiness_summary', r['reason'])
        except Exception:
            pass

    threading.Thread(target=_warm_readiness_cache, daemon=True).start()

    def _warm_market_intel():
        time.sleep(12)
        try:
            from src.groww_auth import fetch_groww_token
            from src.market_context import refresh_market_context
            from src.history_backtest import refresh_backtest_summary
            from src.market_rag import init_knowledge_base
            from src.shadow_learning import init_shadow_tables
            init_knowledge_base()
            init_shadow_tables()
            tok = STATE.get('system.groww_token', '') or fetch_groww_token()
            if tok:
                refresh_market_context(tok)
                refresh_backtest_summary(tok)
            print("📊 Market context + RAG + history backtest warmed")
        except Exception as e:
            print(f"Market intel warm skipped: {e}")

    threading.Thread(target=_warm_market_intel, daemon=True).start()

    now = datetime.now(IST)
    if now.weekday() < 5 and 9 <= now.hour <= 11:
        msg.send(format_morning_brief())
    try:
        scheduler(msg)
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        STATE.set('system.running', False)
        msg.send("🛑 Bot stopped manually.")

if __name__ == '__main__':
    main()
