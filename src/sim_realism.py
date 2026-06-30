"""
Sim realism — Indian options market lessons applied to virtual training.

Sources: NSE monthly BNF expiry (last Tuesday), retail slippage/spread costs,
theta on expiry, 1–2% daily risk cap, premium sweet spot for ₹5k accounts.
"""

import os
import sqlite3
from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')

SIM_MIN_DAYS_TO_EXPIRY = int(os.getenv('SIM_MIN_DAYS_TO_EXPIRY', '5'))
SIM_ROUND_TRIP_COST_RS = float(os.getenv('SIM_ROUND_TRIP_COST_RS', '65'))
SIM_DAILY_LOSS_LIMIT_RS = float(os.getenv('SIM_DAILY_LOSS_LIMIT_RS', '100'))
SIM_BLOCK_EXPIRY_DAY = os.getenv('SIM_BLOCK_EXPIRY_DAY', 'true').lower() == 'true'
SIM_REQUIRE_SWEET_PREMIUM = os.getenv('SIM_REQUIRE_SWEET_PREMIUM', 'true').lower() == 'true'


def _today_shadow_pnl() -> float:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(DB_FILE)
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_rs),0) FROM shadow_trades "
            "WHERE date=? AND status='CLOSED'", (today,)
        ).fetchone()
        conn.close()
        return float(row[0] or 0)
    except Exception:
        return 0.0


def apply_sim_txn_costs(pnl_rs: float) -> float:
    """Brokerage + STT + exchange charges (round trip, one lot)."""
    if SIM_ROUND_TRIP_COST_RS <= 0:
        return pnl_rs
    return round(pnl_rs - SIM_ROUND_TRIP_COST_RS, 0)


def check_sim_entry_gates(premium: float = 0, sim_score: int = 0) -> dict:
    """Pre-open realism gates for virtual trades."""
    from src.expiry_picker import is_expiry_day, is_expiry_week

    boosts = []
    now = datetime.now(IST)
    min_score = int(os.getenv('SIM_MIN_SCORE', '5'))

    if SIM_DAILY_LOSS_LIMIT_RS > 0:
        day_pnl = _today_shadow_pnl()
        if day_pnl <= -SIM_DAILY_LOSS_LIMIT_RS:
            return {
                'ok': False,
                'reason': (
                    f'daily loss cap ₹{SIM_DAILY_LOSS_LIMIT_RS:.0f} hit '
                    f'(today ₹{day_pnl:.0f}) — 2% capital rule'
                ),
                'boosts': boosts,
            }

    if SIM_BLOCK_EXPIRY_DAY and is_expiry_day():
        if now.time() >= dtime(12, 0):
            return {
                'ok': False,
                'reason': 'expiry afternoon — theta crush zone, no new buys',
                'boosts': boosts,
            }
        boosts.append('expiry_day')

    if is_expiry_week():
        boosts.append('expiry_week')
        need = min_score + 1
        if sim_score and sim_score < need:
            return {
                'ok': False,
                'reason': f'expiry week — need sim score ≥{need} (theta risk)',
                'boosts': boosts,
            }

    if SIM_REQUIRE_SWEET_PREMIUM and premium > 0:
        from src.wr_filters import check_premium_sweet_spot
        sweet = check_premium_sweet_spot(premium)
        if not sweet.get('ok'):
            return {'ok': False, 'reason': sweet.get('reason', 'premium band'), 'boosts': boosts}

    return {'ok': True, 'reason': '', 'boosts': boosts}


def realism_status() -> dict:
    """Dashboard snapshot of active realism rules."""
    from src.expiry_picker import banknifty_monthly_expiry, is_expiry_day, is_expiry_week

    exp = banknifty_monthly_expiry()
    today = datetime.now(IST).date()
    return {
        'min_days_to_expiry': SIM_MIN_DAYS_TO_EXPIRY,
        'round_trip_cost_rs': SIM_ROUND_TRIP_COST_RS,
        'daily_loss_limit_rs': SIM_DAILY_LOSS_LIMIT_RS,
        'block_expiry_day': SIM_BLOCK_EXPIRY_DAY,
        'require_sweet_premium': SIM_REQUIRE_SWEET_PREMIUM,
        'is_expiry_day': is_expiry_day(),
        'is_expiry_week': is_expiry_week(),
        'days_to_expiry': (exp - today).days,
        'monthly_expiry': exp.strftime('%d %b %Y'),
        'today_shadow_pnl': _today_shadow_pnl(),
    }
