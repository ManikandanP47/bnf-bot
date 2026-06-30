"""
July 2026 training month — single calendar for sim, paper, wallet, recovery.

TRAINING_START_DATE=2026-07-01 → 30 days before live ₹5k.
Days 1–15: SIM (virtual wallet ₹10k+, multi-order, recovery)
Days 16–30: PAPER (/execute, still no live money)
Aug 1+: LIVE_READY window (still needs /readiness gates)
"""

import os
from datetime import datetime, date, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

TRAINING_START_DATE = os.getenv('TRAINING_START_DATE', '2026-07-01')
TRAINING_MONTH_DAYS = int(os.getenv('TRAINING_MONTH_DAYS', '30'))
SIM_ONLY_DAYS = int(os.getenv('SIM_ONLY_DAYS', '15'))
PAPER_PHASE_DAYS = int(os.getenv('PAPER_PHASE_DAYS', '15'))


def _parse_date(s: str) -> date:
    return datetime.strptime(s, '%Y-%m-%d').date()


def training_anchor_date() -> date:
    """Official start — ignores pre-start DB noise (e.g. June tests)."""
    try:
        return _parse_date(TRAINING_START_DATE)
    except ValueError:
        return datetime.now(IST).date()


def today_ist() -> date:
    return datetime.now(IST).date()


def training_day_number() -> int:
    """1 on first training day; 0 before start."""
    t = today_ist()
    anchor = training_anchor_date()
    if t < anchor:
        return 0
    return (t - anchor).days + 1


def training_elapsed_days() -> int:
    """0 on day 1, 1 on day 2, …"""
    d = training_day_number()
    return max(0, d - 1)


def training_week_number() -> int:
    d = training_day_number()
    if d <= 0:
        return 1
    return min(5, (d - 1) // 7 + 1)


def days_until_start() -> int:
    return max(0, (training_anchor_date() - today_ist()).days)


def days_until_paper_calendar() -> int:
    d = training_day_number()
    if d <= 0:
        return days_until_start() + SIM_ONLY_DAYS
    if d > SIM_ONLY_DAYS:
        return 0
    return SIM_ONLY_DAYS - d + 1


def days_until_live_calendar() -> int:
    d = training_day_number()
    if d <= 0:
        return days_until_start() + TRAINING_MONTH_DAYS
    return max(0, TRAINING_MONTH_DAYS - d + 1)


def is_pre_training() -> bool:
    return today_ist() < training_anchor_date()


def is_training_month_active() -> bool:
    d = training_day_number()
    return 0 < d <= TRAINING_MONTH_DAYS


def is_live_money_allowed() -> bool:
    """No live ₹5k until training month complete + readiness."""
    if os.getenv('PAPER_MODE', 'true').lower() == 'true':
        return False
    if training_day_number() <= TRAINING_MONTH_DAYS:
        return False
    try:
        from src.brain_metrics import assess_live_readiness
        return assess_live_readiness().get('ready', False)
    except Exception:
        return False


def init_all_training_tables():
    """Ensure every training subsystem DB is ready."""
    from src.shadow_learning import init_shadow_tables
    from src.virtual_broker import init_virtual_broker_tables
    from src.sim_market_learn import init_learning_log_table
    from src.loss_recovery import init_recovery_tables
    init_shadow_tables()
    init_virtual_broker_tables()
    init_learning_log_table()
    init_recovery_tables()
    try:
        from src.sim_scan_journal import init_sim_scan_table
        init_sim_scan_table()
    except Exception:
        pass


def verify_training_stack() -> dict:
    """Startup health — all features we built must be importable + enabled."""
    checks = []

    def ok(name, cond, detail=''):
        checks.append({'name': name, 'ok': bool(cond), 'detail': detail})

    ok('MARKET_SIM', os.getenv('MARKET_SIM', 'true').lower() == 'true')
    ok('LIVE_LEARNING', os.getenv('LIVE_LEARNING', 'true').lower() == 'true')
    ok('GREEKS_ENABLED', os.getenv('GREEKS_ENABLED', 'true').lower() == 'true')
    ok('RECOVERY_ENABLED', os.getenv('RECOVERY_ENABLED', 'true').lower() == 'true')
    ok('SIM_MULTI_FROM_WEEK1', os.getenv('SIM_MULTI_FROM_WEEK1', 'true').lower() == 'true')
    ok('SIM_LEARNING_LOG', os.getenv('SIM_LEARNING_LOG', 'true').lower() == 'true')
    ok('PAPER_MODE', os.getenv('PAPER_MODE', 'true').lower() == 'true', 'must stay true in July')
    ok('TRAINING_START', training_day_number() >= 0,
       f"starts {TRAINING_START_DATE}")

    try:
        from src.sim_wallet import wallet_core, build_sim_wallet_payload
        w = wallet_core()
        ok('SIM_WALLET', w.get('week_base_rs', 0) >= 10000,
           f"W{w.get('week')} ₹{w.get('week_base_rs', 0):,.0f}")
        build_sim_wallet_payload()
        ok('SIM_WALLET_API', True)
    except Exception as e:
        ok('SIM_WALLET', False, str(e)[:50])

    try:
        from src.loss_recovery import recovery_status
        recovery_status()
        ok('LOSS_RECOVERY', True)
    except Exception as e:
        ok('LOSS_RECOVERY', False, str(e)[:50])

    try:
        from src.live_learning import run_active_learning_cycle
        from agents.learning_agent import BRAIN
        ok('LIVE_LEARNING_MOD', True)
    except Exception as e:
        ok('LIVE_LEARNING_MOD', False, str(e)[:50])

    try:
        from src.greeks_gates import check_iv_rank_for_buyers
        ok('IV_RANK_GATES', True)
    except Exception as e:
        ok('IV_RANK_GATES', False, str(e)[:50])

    try:
        from src.option_greeks import get_greeks_dashboard
        ok('GREEKS_DASH', True)
    except Exception as e:
        ok('GREEKS_DASH', False, str(e)[:50])

    failed = [c for c in checks if not c['ok']]
    return {
        'all_ok': len(failed) == 0,
        'checks': checks,
        'failed': failed,
        'training_day': training_day_number(),
        'phase_hint': _phase_hint(),
    }


def _phase_hint() -> str:
    d = training_day_number()
    if d == 0:
        return f'PRE_START (starts {TRAINING_START_DATE})'
    if d <= SIM_ONLY_DAYS:
        return f'SIM day {d}/{SIM_ONLY_DAYS}'
    if d <= TRAINING_MONTH_DAYS:
        return f'PAPER day {d - SIM_ONLY_DAYS}/{PAPER_PHASE_DAYS}'
    return 'LIVE_READY_WINDOW'


def format_july_kickoff_message() -> str:
    """Day 1 morning — everything that trains in July."""
    v = verify_training_stack()
    wk = training_week_number()
    return (
        f"🎓 *July Training Month — DAY 1*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *{TRAINING_START_DATE} → 30 days* before live ₹5k\n\n"
        f"*Week {wk} sim wallet:* ₹10,000 virtual\n"
        f"  • Multi-order (2 open) + recovery ON\n"
        f"  • Live learning every 60s + market observer 9:16\n"
        f"  • IV-rank gates + Greeks on every scan\n"
        f"  • Daily loss cap 2% of week capital\n\n"
        f"*Schedule*\n"
        f"  Jul 1–15: *SIM only* (₹0 risk, virtual wallet)\n"
        f"  Jul 16–31: *PAPER* `/execute` (still PAPER_MODE)\n"
        f"  Aug 1+: Live window if `/readiness` ✅\n\n"
        f"*Dashboard:* Sim & Learning tab — wallet, orders, recovery\n"
        f"*Commands:* /training /shadow /recovery /simday\n\n"
        f"Stack: {'✅ all systems' if v['all_ok'] else '⚠️ ' + str(len(v['failed'])) + ' check(s) failed'}\n"
        f"_No live money this month — train every aspect first._ 🛡️"
    )


def format_pre_start_message() -> str:
    return (
        f"⏳ *Training starts {TRAINING_START_DATE}*\n"
        f"({days_until_start()} day(s) — bot can warm up scans today)\n"
        f"July plan: 15d sim → 15d paper → live gates in August.\n"
        f"Type /training for status."
    )


def format_daily_training_brief() -> str:
    d = training_day_number()
    from src.shadow_learning import learning_phase_info, training_phase
    info = learning_phase_info()
    phase = training_phase()
    try:
        from src.sim_wallet import wallet_core
        w = wallet_core()
        wallet_line = (
            f"Wallet W{w.get('week')}: ₹{w.get('balance', 0):,.0f} "
            f"(today ₹{w.get('week_pnl', 0):+,.0f})"
        )
    except Exception:
        wallet_line = ''

    return (
        f"📋 *Training Day {d}/{TRAINING_MONTH_DAYS}* — {phase}\n"
        f"Valid SIM: {info.get('shadow_today', 0)} sims today | "
        f"paper in {info.get('days_until_paper', 0)}d\n"
        f"{wallet_line}\n"
        f"_Type /training for full dashboard_"
    )


def bootstrap_training_month(messenger=None) -> dict:
    """Called on every bot start."""
    init_all_training_tables()
    v = verify_training_stack()
    try:
        from core.shared_state import STATE
        STATE.set('training.calendar', {
            'start': TRAINING_START_DATE,
            'day': training_day_number(),
            'week': training_week_number(),
            'phase_hint': _phase_hint(),
            'stack_ok': v['all_ok'],
        })
    except Exception:
        pass
    return v


def send_training_messages_if_due(messenger, last_kickoff: int, last_daily: int, now) -> tuple:
    """Returns updated (last_kickoff, last_daily) day ids."""
    if not messenger:
        return last_kickoff, last_daily

    d = training_day_number()
    if is_pre_training() and now.day != last_kickoff:
        # once per calendar day before start
        if now.hour == 9 and 5 <= now.minute <= 10:
            messenger.send(format_pre_start_message())
            return now.day, last_daily

    if d == 1 and now.hour == 9 and 6 <= now.minute <= 12 and last_kickoff != now.day:
        messenger.send(format_july_kickoff_message())
        return now.day, last_daily

    if is_training_month_active() and d > 1:
        if now.hour == 9 and 7 <= now.minute <= 11 and last_daily != now.day:
            messenger.send(format_daily_training_brief())
            return last_kickoff, now.day

    return last_kickoff, last_daily
