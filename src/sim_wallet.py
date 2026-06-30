"""
Virtual sim wallet — train capital protection, loss reduction, and recovery.

Starts at SIM_WALLET_START_RS (₹5k), goal ₹10k, scales to ₹15–20k for 2-lot training.
Balance = start + cumulative closed sim P&L (txn costs included). Open orders reserve margin.
"""

import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')

SIM_WALLET_START_RS = float(os.getenv('SIM_WALLET_START_RS', '5000'))
SIM_WALLET_TARGET_RS = float(os.getenv('SIM_WALLET_TARGET_RS', '10000'))
SIM_WALLET_SCALE_RS = float(os.getenv('SIM_WALLET_SCALE_RS', '15000'))
SIM_WALLET_MAX_RS = float(os.getenv('SIM_WALLET_MAX_RS', '20000'))
SIM_WALLET_LOT_UNIT = int(os.getenv('SIM_WALLET_LOT_UNIT', '15'))
SIM_WALLET_MAX_LOTS = int(os.getenv('SIM_WALLET_MAX_LOTS', '2'))
SIM_WALLET_RESERVE_PCT = float(os.getenv('SIM_WALLET_RESERVE_PCT', '0.15'))


def _conn():
    from src.db_persistence import connect
    return connect()


def _today() -> str:
    return datetime.now(IST).strftime('%Y-%m-%d')


def capital_phase(balance: float) -> dict:
    """Training stage from virtual balance."""
    if balance < SIM_WALLET_TARGET_RS:
        return {
            'phase': '5K_TRAIN',
            'label': '₹5k → ₹10k training',
            'next_goal': SIM_WALLET_TARGET_RS,
            'max_lots': 1,
        }
    if balance < SIM_WALLET_SCALE_RS:
        return {
            'phase': '10K_GROW',
            'label': '₹10k reached — grow to ₹15k',
            'next_goal': SIM_WALLET_SCALE_RS,
            'max_lots': 1,
        }
    if balance < SIM_WALLET_MAX_RS:
        return {
            'phase': '15K_SCALE',
            'label': '₹15k — 2-lot training unlocked',
            'next_goal': SIM_WALLET_MAX_RS,
            'max_lots': SIM_WALLET_MAX_LOTS,
        }
    return {
        'phase': '20K_MULTI',
        'label': '₹20k — multi-lot comfort zone',
        'next_goal': SIM_WALLET_MAX_RS,
        'max_lots': SIM_WALLET_MAX_LOTS,
    }


def lots_for_balance(balance: float) -> int:
    phase = capital_phase(balance)
    if phase['max_lots'] <= 1:
        return 1
    # 2 lots only if one lot < 45% of balance (room for SL + recovery)
    per_lot_budget = balance * (1 - SIM_WALLET_RESERVE_PCT) / 2
    if per_lot_budget >= 3500:
        return min(SIM_WALLET_MAX_LOTS, 2)
    return 1


def _open_reserved() -> float:
    try:
        from src.shadow_learning import init_shadow_tables
        init_shadow_tables()
        conn = _conn()
        rows = conn.execute("""
            SELECT entry_prem, COALESCE(lots, 1) FROM shadow_trades
            WHERE status='OPEN'
        """).fetchall()
        conn.close()
        return sum(float(p or 0) * SIM_WALLET_LOT_UNIT * int(l or 1) for p, l in rows)
    except Exception:
        return 0.0


def _cumulative_pnl() -> float:
    try:
        conn = _conn()
        row = conn.execute("""
            SELECT COALESCE(SUM(pnl_rs), 0) FROM shadow_trades
            WHERE status='CLOSED' AND pnl_rs IS NOT NULL
        """).fetchone()
        conn.close()
        return float(row[0] or 0)
    except Exception:
        return 0.0


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


def wallet_core() -> dict:
    cum = _cumulative_pnl()
    balance = round(SIM_WALLET_START_RS + cum, 0)
    reserved = round(_open_reserved(), 0)
    available = round(max(0, balance - reserved), 0)
    phase = capital_phase(balance)
    lots = lots_for_balance(balance)
    goal = phase['next_goal']
    progress = min(100, round((balance - SIM_WALLET_START_RS) /
                              max(goal - SIM_WALLET_START_RS, 1) * 100, 1))
    if balance >= SIM_WALLET_MAX_RS:
        progress = 100
    return {
        'start_rs': SIM_WALLET_START_RS,
        'target_rs': SIM_WALLET_TARGET_RS,
        'scale_rs': SIM_WALLET_SCALE_RS,
        'max_rs': SIM_WALLET_MAX_RS,
        'balance': balance,
        'available': available,
        'reserved': reserved,
        'cumulative_pnl': round(cum, 0),
        'lots_allowed': lots,
        'phase': phase['phase'],
        'phase_label': phase['label'],
        'next_goal_rs': goal,
        'progress_pct': progress,
    }


def plan_sim_order(premium: float, is_recovery: bool = False) -> dict:
    """Check virtual wallet can afford order; return lots/qty."""
    if premium <= 0:
        return {'ok': False, 'reason': 'no premium'}
    w = wallet_core()
    lots = 1 if is_recovery else lots_for_balance(w['balance'])
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
    return {
        'ok': True,
        'lots': lots,
        'qty': lots * SIM_WALLET_LOT_UNIT,
        'lot_cost': round(lot_cost, 0),
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


def get_sim_orders(limit: int = 30) -> list:
    """Recent virtual orders for dashboard."""
    try:
        from src.shadow_learning import init_shadow_tables
        init_shadow_tables()
        conn = _conn()
        rows = conn.execute("""
            SELECT id, date, entry_time, exit_time, option_name, bias, session,
                   entry_prem, exit_prem, pnl_rs, outcome, exit_reason, status,
                   sim_source, sim_score, COALESCE(lots, 1), COALESCE(is_recovery, 0),
                   peak_pnl_rs
            FROM shadow_trades ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        keys = [
            'id', 'date', 'entry_time', 'exit_time', 'option_name', 'bias', 'session',
            'entry_prem', 'exit_prem', 'pnl_rs', 'outcome', 'exit_reason', 'status',
            'sim_source', 'sim_score', 'lots', 'is_recovery', 'peak_pnl_rs',
        ]
        return [dict(zip(keys, r)) for r in rows]
    except Exception:
        return []


def build_sim_wallet_payload() -> dict:
    """Full dashboard payload: wallet, orders, recovery."""
    w = wallet_core()
    today = _today_pnl_split()
    orders = get_sim_orders(25)
    today_orders = [o for o in orders if o.get('date') == _today()]

    recovery = {}
    try:
        from src.loss_recovery import recovery_status
        recovery = recovery_status()
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
        'all_time': all_time,
        'orders': today_orders,
        'orders_recent': orders[:15],
        'recovery': recovery,
    }
