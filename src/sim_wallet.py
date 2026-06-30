"""
Virtual sim wallet — weekly capital ladder + multi-order + recovery training.

Pro training (PRO_TRAINING_MODE=true, default July):
  Week 1: ₹25k → W2 ₹35k → W3 ₹50k → W4+ ₹75k (3 lots, 3 open)

Legacy ladder: ₹10k → ₹12.5k → ₹15k → ₹20k
"""

import os
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

SIM_WALLET_LOT_UNIT = int(os.getenv('SIM_WALLET_LOT_UNIT', '15'))
PRO_TRAINING_MODE = os.getenv('PRO_TRAINING_MODE', 'true').lower() == 'true'

# Pro training capital ladder (July sim — room for multi-strike habits)
if PRO_TRAINING_MODE:
    _W1 = os.getenv('SIM_WALLET_WEEK1_RS', '25000')
    _W2 = os.getenv('SIM_WALLET_WEEK2_RS', '35000')
    _W3 = os.getenv('SIM_WALLET_WEEK3_RS', '50000')
    _W4 = os.getenv('SIM_WALLET_WEEK4_RS', '75000')
else:
    _W1 = os.getenv('SIM_WALLET_WEEK1_RS', '10000')
    _W2 = os.getenv('SIM_WALLET_WEEK2_RS', '12500')
    _W3 = os.getenv('SIM_WALLET_WEEK3_RS', '15000')
    _W4 = os.getenv('SIM_WALLET_WEEK4_RS', '20000')

WEEKLY_CAPITAL = [float(_W1), float(_W2), float(_W3), float(_W4)]

if PRO_TRAINING_MODE:
    SIM_WALLET_MAX_LOTS = int(os.getenv('SIM_WALLET_MAX_LOTS', '3'))
    SIM_WALLET_MAX_OPEN = int(os.getenv('SIM_WALLET_MAX_OPEN', '3'))
    SIM_MIN_LOT_BUDGET_RS = float(os.getenv('SIM_MIN_LOT_BUDGET_RS', '3500'))
else:
    SIM_WALLET_MAX_LOTS = int(os.getenv('SIM_WALLET_MAX_LOTS', '2'))
    SIM_WALLET_MAX_OPEN = int(os.getenv('SIM_WALLET_MAX_OPEN', '2'))
    SIM_MIN_LOT_BUDGET_RS = float(os.getenv('SIM_MIN_LOT_BUDGET_RS', '2500'))

SIM_WALLET_RESERVE_PCT = float(os.getenv('SIM_WALLET_RESERVE_PCT', '0.10'))
SIM_WALLET_DAILY_LOSS_PCT = float(os.getenv('SIM_WALLET_DAILY_LOSS_PCT', '0.02'))
SIM_MULTI_FROM_WEEK1 = os.getenv('SIM_MULTI_FROM_WEEK1', 'true').lower() == 'true'


def _conn():
    from src.db_persistence import connect
    return connect()


def _today() -> str:
    return datetime.now(IST).strftime('%Y-%m-%d')


def get_training_week() -> dict:
    """Training week from official calendar (not pre-July DB noise)."""
    from src.training_calendar import (
        training_anchor_date, training_week_number, training_day_number,
        days_until_start, TRAINING_START_DATE,
    )
    anchor = training_anchor_date()
    today = datetime.now(IST).date()
    if today < anchor:
        return {
            'week': 1,
            'week_start': anchor.isoformat(),
            'week_start_display': anchor.strftime('%d %b'),
            'days_in_week': 0,
            'week_base_rs': week_base_capital(1),
            'next_week_base_rs': week_base_capital(2),
            'pre_start': True,
            'starts_in_days': days_until_start(),
            'start_date': TRAINING_START_DATE,
        }
    days = (today - anchor).days
    week_num = training_week_number()
    week_start = anchor + timedelta(days=(week_num - 1) * 7)
    next_w = min(week_num + 1, len(WEEKLY_CAPITAL))
    return {
        'week': week_num,
        'week_start': week_start.isoformat(),
        'week_start_display': week_start.strftime('%d %b'),
        'days_in_week': (today - week_start).days + 1,
        'week_base_rs': week_base_capital(week_num),
        'next_week_base_rs': week_base_capital(next_w),
        'training_day': training_day_number(),
        'pre_start': False,
    }


def week_base_capital(week: int) -> float:
    idx = min(max(1, week), len(WEEKLY_CAPITAL)) - 1
    return WEEKLY_CAPITAL[idx]


def _pnl_since(date_from: str) -> float:
    try:
        from src.shadow_learning import init_shadow_tables
        init_shadow_tables()
        conn = _conn()
        row = conn.execute("""
            SELECT COALESCE(SUM(pnl_rs), 0) FROM shadow_trades
            WHERE status='CLOSED' AND date >= ? AND pnl_rs IS NOT NULL
        """, (date_from,)).fetchone()
        conn.close()
        return float(row[0] or 0)
    except Exception:
        return 0.0


def _open_reserved() -> float:
    try:
        from src.shadow_learning import init_shadow_tables
        init_shadow_tables()
        conn = _conn()
        rows = conn.execute("""
            SELECT entry_prem, COALESCE(lots, 1) FROM shadow_trades WHERE status='OPEN'
        """).fetchall()
        conn.close()
        return sum(float(p or 0) * SIM_WALLET_LOT_UNIT * int(l or 1) for p, l in rows)
    except Exception:
        return 0.0


def effective_daily_loss_cap() -> float:
    """2% of this week's base capital (salary-trader rule)."""
    tw = get_training_week()
    base = tw['week_base_rs']
    pct_cap = round(base * SIM_WALLET_DAILY_LOSS_PCT, 0)
    floor = float(os.getenv('SIM_DAILY_LOSS_LIMIT_RS', '100'))
    return max(floor, pct_cap)


def is_account_dead_today() -> dict:
    """No new sims when today's loss breaches daily cap."""
    today = _today_pnl_split()
    cap = effective_daily_loss_cap()
    pnl = today['pnl']
    remaining = round(cap + pnl, 0)
    dead = pnl <= -cap
    return {
        'dead': dead,
        'cap_rs': cap,
        'today_pnl': pnl,
        'remaining_rs': max(0, remaining),
        'pct_of_week_base': round(SIM_WALLET_DAILY_LOSS_PCT * 100, 1),
    }


def lots_for_balance(balance: float, week: int = 1) -> int:
    """Multi-lot from week 1 when wallet can afford it."""
    if not SIM_MULTI_FROM_WEEK1 and week < 2:
        return 1
    max_lots = SIM_WALLET_MAX_LOTS
    deployable = balance * (1 - SIM_WALLET_RESERVE_PCT)
    per_slot = deployable / max_lots
    if per_slot >= SIM_MIN_LOT_BUDGET_RS:
        return max_lots
    if deployable >= SIM_MIN_LOT_BUDGET_RS:
        return 1
    return 1


def capital_phase(balance: float, week: int, week_base: float) -> dict:
    target = week_base * 1.10
    if balance >= week_base * 1.15:
        label = f'Week {week} · ₹{week_base:,.0f} — ahead of plan'
    elif balance >= week_base:
        label = f'Week {week} · ₹{week_base:,.0f} — on track'
    else:
        label = f'Week {week} · ₹{week_base:,.0f} — drawdown, protect capital'
    return {
        'phase': f'WEEK_{week}',
        'label': label,
        'next_goal': round(target, 0),
        'max_lots': SIM_WALLET_MAX_LOTS if SIM_MULTI_FROM_WEEK1 else 1,
        'max_open': SIM_WALLET_MAX_OPEN,
    }


def _today_pnl_split() -> dict:
    from src.shadow_learning import init_shadow_tables
    init_shadow_tables()
    today = _today()
    conn = _conn()
    row = conn.execute("""
        SELECT
            COALESCE(SUM(pnl_rs), 0),
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(is_recovery, 0)=1 THEN pnl_rs ELSE 0 END),
            SUM(CASE WHEN COALESCE(is_recovery, 0)=1 THEN 1 ELSE 0 END)
        FROM shadow_trades WHERE date=? AND status='CLOSED'
    """, (today,)).fetchone()
    open_n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchone()[0]
    conn.close()
    return {
        'pnl': float(row[0] or 0),
        'wins': int(row[1] or 0),
        'losses': int(row[2] or 0),
        'recovery_pnl': float(row[3] or 0),
        'recovery_trades': int(row[4] or 0),
        'open': int(open_n or 0),
    }


def _week_pnl_split(week_start: str) -> dict:
    from src.shadow_learning import init_shadow_tables
    init_shadow_tables()
    conn = _conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(pnl_rs), 0),
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END)
        FROM shadow_trades WHERE date >= ? AND status='CLOSED'
    """, (week_start,)).fetchone()
    conn.close()
    return {
        'pnl': float(row[0] or 0),
        'wins': int(row[1] or 0),
        'losses': int(row[2] or 0),
    }


def wallet_core() -> dict:
    tw = get_training_week()
    week = tw['week']
    week_base = tw['week_base_rs']
    week_start = tw['week_start']
    week_pnl = _pnl_since(week_start)
    cum_all = _pnl_since('2000-01-01')

    balance = round(week_base + week_pnl, 0)
    reserved = round(_open_reserved(), 0)
    available = round(max(0, balance - reserved), 0)
    phase = capital_phase(balance, week, week_base)
    lots = lots_for_balance(balance, week)
    goal = phase['next_goal']
    progress = min(100, round((balance - week_base) / max(goal - week_base, 1) * 100, 1))
    if balance >= goal:
        progress = 100

    dead = is_account_dead_today()
    return {
        'week': week,
        'week_start': week_start,
        'week_start_display': tw['week_start_display'],
        'days_in_week': tw['days_in_week'],
        'week_base_rs': week_base,
        'next_week_base_rs': tw['next_week_base_rs'],
        'weekly_capital_ladder': WEEKLY_CAPITAL,
        'pro_training_mode': PRO_TRAINING_MODE,
        'balance': balance,
        'available': available,
        'reserved': reserved,
        'week_pnl': round(week_pnl, 0),
        'cumulative_pnl': round(cum_all, 0),
        'lots_allowed': lots,
        'max_open': SIM_WALLET_MAX_OPEN,
        'multi_order': SIM_MULTI_FROM_WEEK1,
        'phase': phase['phase'],
        'phase_label': phase['label'],
        'next_goal_rs': goal,
        'progress_pct': progress,
        'daily_loss_cap_rs': dead['cap_rs'],
        'account_dead_today': dead['dead'],
        'daily_remaining_rs': dead['remaining_rs'],
    }


def max_loss_at_risk(entry_prem: float, sl_prem: float, lots: int = 1) -> float:
    """Rupee risk if SL hits (per trade)."""
    if not entry_prem or not sl_prem:
        return 0.0
    return round(max(0, (entry_prem - sl_prem) * SIM_WALLET_LOT_UNIT * lots), 0)


def plan_sim_order(premium: float, sl_prem: float = 0, is_recovery: bool = False) -> dict:
    if premium <= 0:
        return {'ok': False, 'reason': 'no premium'}

    dead = is_account_dead_today()
    if dead['dead']:
        return {
            'ok': False,
            'reason': (
                f"🛑 Sim account dead today — loss ₹{abs(dead['today_pnl']):,.0f} "
                f"hit {dead['pct_of_week_base']}% cap (₹{dead['cap_rs']:,.0f})"
            ),
        }

    w = wallet_core()
    lots = 1 if is_recovery else lots_for_balance(w['balance'], w['week'])
    lot_cost = premium * SIM_WALLET_LOT_UNIT * lots
    if lot_cost > w['available']:
        if lots > 1:
            lots = 1
            lot_cost = premium * SIM_WALLET_LOT_UNIT
        if lot_cost > w['available']:
            return {
                'ok': False,
                'reason': (
                    f"virtual wallet ₹{w['available']:,.0f} free — "
                    f"need ₹{lot_cost:,.0f} for {lots} lot(s)"
                ),
                'wallet': w,
            }

    sl = sl_prem or round(premium * 0.75, 0)
    risk = max_loss_at_risk(premium, sl, lots)
    return {
        'ok': True,
        'lots': lots,
        'qty': lots * SIM_WALLET_LOT_UNIT,
        'lot_cost': round(lot_cost, 0),
        'max_loss_rs': risk,
        'is_recovery': is_recovery,
        'wallet': w,
    }


def is_recovery_sim_window() -> bool:
    try:
        from core.shared_state import STATE
        r = STATE.get('recovery') or {}
        return bool(r.get('active')) and not r.get('used_today')
    except Exception:
        return False


def _enrich_order(row: dict) -> dict:
    lots = int(row.get('lots') or 1)
    entry = float(row.get('entry_prem') or 0)
    sl = float(row.get('sl_prem') or entry * 0.75)
    row['max_loss_rs'] = max_loss_at_risk(entry, sl, lots)
    row['lot_cost'] = row.get('lot_cost') or round(entry * SIM_WALLET_LOT_UNIT * lots, 0)
    row['is_recovery'] = bool(row.get('is_recovery'))
    return row


def get_sim_orders(limit: int = 30) -> list:
    try:
        from src.shadow_learning import init_shadow_tables
        init_shadow_tables()
        conn = _conn()
        rows = conn.execute("""
            SELECT id, date, entry_time, exit_time, option_name, bias, session,
                   entry_prem, exit_prem, sl_prem, pnl_rs, outcome, exit_reason, status,
                   sim_source, sim_score, COALESCE(lots, 1), COALESCE(is_recovery, 0),
                   peak_pnl_rs, COALESCE(lot_cost, 0)
            FROM shadow_trades ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        keys = [
            'id', 'date', 'entry_time', 'exit_time', 'option_name', 'bias', 'session',
            'entry_prem', 'exit_prem', 'sl_prem', 'pnl_rs', 'outcome', 'exit_reason',
            'status', 'sim_source', 'sim_score', 'lots', 'is_recovery', 'peak_pnl_rs',
            'lot_cost',
        ]
        return [_enrich_order(dict(zip(keys, r))) for r in rows]
    except Exception:
        return []


def build_live_compare() -> dict:
    """Side-by-side: weekly sim wallet vs real ₹5k paper/live."""
    from src.capital_guard import LIVE_CAPITAL_RS
    today_sim = _today_pnl_split()
    w = wallet_core()
    live_today = 0.0
    live_week = 0.0
    try:
        from core.shared_state import STATE
        live_today = float(STATE.get('brain.today_pnl', 0) or 0)
        live_week = float(STATE.get('system.week_pnl', 0) or 0)
    except Exception:
        pass
    tw = get_training_week()
    week_sim = _week_pnl_split(tw['week_start'])
    return {
        'sim': {
            'label': f"Week {w['week']} sim wallet",
            'capital_base': w['week_base_rs'],
            'balance': w['balance'],
            'today_pnl': today_sim['pnl'],
            'week_pnl': week_sim['pnl'],
            'open_positions': today_sim['open'],
            'max_open': w['max_open'],
            'lots_allowed': w['lots_allowed'],
        },
        'live': {
            'label': 'Real / paper (your money)',
            'capital': LIVE_CAPITAL_RS,
            'today_pnl': live_today,
            'week_pnl': live_week,
            'note': 'Stays ₹5k until /readiness — sim ladder is training only',
        },
        'gap_rs': round(today_sim['pnl'] - live_today, 0),
    }


def build_sim_wallet_payload() -> dict:
    w = wallet_core()
    today = _today_pnl_split()
    tw = get_training_week()
    week = _week_pnl_split(tw['week_start'])
    orders = get_sim_orders(25)
    today_orders = [o for o in orders if o.get('date') == _today()]

    recovery = {}
    try:
        from src.loss_recovery import recovery_status
        recovery = recovery_status()
        recovery['enabled_from_week1'] = True
        recovery['multi_order'] = SIM_MULTI_FROM_WEEK1
    except Exception:
        pass

    all_time = {'wins': 0, 'losses': 0, 'trades': 0}
    try:
        conn = _conn()
        row = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END)
            FROM shadow_trades WHERE status='CLOSED'
        """).fetchone()
        conn.close()
        all_time = {
            'trades': int(row[0] or 0),
            'wins': int(row[1] or 0),
            'losses': int(row[2] or 0),
        }
        t = all_time['wins'] + all_time['losses']
        all_time['win_rate'] = round(all_time['wins'] / t * 100, 1) if t else None
    except Exception:
        pass

    return {
        'wallet': w,
        'today': today,
        'week': week,
        'training_week': tw,
        'account_status': is_account_dead_today(),
        'live_compare': build_live_compare(),
        'all_time': all_time,
        'orders': today_orders,
        'orders_recent': orders[:20],
        'recovery': recovery,
    }
