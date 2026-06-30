"""
Valid training days — calendar days ≠ learning days.

A SIM day counts only if evidence proves the bot scanned the market
(MIN_SCANS_VALID_DAY scans) or closed at least one virtual trade.
"""

import json
import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
MIN_SCANS_VALID_DAY = int(os.getenv('MIN_SCANS_VALID_DAY', '3'))
VALID_DAYS_KEY = 'valid_training_days'


def _conn():
    from src.db_persistence import connect
    return connect()


def _ensure_flags(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_flags (key TEXT PRIMARY KEY, value TEXT)
    """)


def _load_valid_days() -> dict:
    conn = _conn()
    _ensure_flags(conn)
    row = conn.execute(
        'SELECT value FROM bot_flags WHERE key=?', (VALID_DAYS_KEY,)
    ).fetchone()
    conn.close()
    if not row:
        return {'sim': [], 'paper': []}
    try:
        data = json.loads(row[0])
        data.setdefault('sim', [])
        data.setdefault('paper', [])
        return data
    except json.JSONDecodeError:
        return {'sim': [], 'paper': []}


def _save_valid_days(data: dict):
    conn = _conn()
    _ensure_flags(conn)
    conn.execute(
        'INSERT OR REPLACE INTO bot_flags (key, value) VALUES (?, ?)',
        (VALID_DAYS_KEY, json.dumps(data)),
    )
    conn.commit()
    conn.close()


def evaluate_day(date: str = None) -> dict:
    """Check if a date qualifies as a valid training day."""
    date = date or datetime.now(IST).strftime('%Y-%m-%d')
    from src.sim_evidence import get_daily_counts
    from src.shadow_learning import training_phase

    c = get_daily_counts(date)
    scans = c.get('scans_total', 0)
    closed = c.get('shadow_closed', 0)
    phase = training_phase()

    sim_valid = scans >= MIN_SCANS_VALID_DAY or closed > 0
    paper_valid = c.get('funnel_events', 0) > 0 or c.get('shadow_opened', 0) > 0
    # paper: at least one execute path event or paper trade in trades table
    try:
        conn = _conn()
        paper_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE date=? AND outcome IS NOT NULL",
            (date,),
        ).fetchone()[0]
        conn.close()
        if paper_trades > 0:
            paper_valid = True
    except Exception:
        pass

    return {
        'date': date,
        'phase': phase,
        'scans': scans,
        'shadow_closed': closed,
        'sim_valid': sim_valid,
        'paper_valid': paper_valid,
        'reason': (
            f'{scans} scans (need {MIN_SCANS_VALID_DAY})'
            if not sim_valid else f'{scans} scans | {closed} sim closes'
        ),
    }


def mark_today_if_valid() -> dict:
    """Called at EOD — persist valid day if criteria met."""
    today = datetime.now(IST).strftime('%Y-%m-%d')
    ev = evaluate_day(today)
    data = _load_valid_days()
    from src.shadow_learning import training_phase

    phase = training_phase()
    marked = False
    if phase == 'SIM' and ev['sim_valid'] and today not in data['sim']:
        data['sim'].append(today)
        marked = True
    elif phase == 'PAPER' and ev['paper_valid'] and today not in data['paper']:
        data['paper'].append(today)
        marked = True

    if marked:
        _save_valid_days(data)
        try:
            from src.sim_evidence import record_evidence
            record_evidence('VALID_DAY_MARKED', {
                'date': today, 'phase': phase, **ev,
            })
        except Exception:
            pass

    return {'marked': marked, 'valid_days': data, 'evaluation': ev}


def get_valid_day_counts() -> dict:
    data = _load_valid_days()
    from src.shadow_learning import SIM_ONLY_DAYS, PAPER_PHASE_DAYS
    return {
        'sim_valid': len(data['sim']),
        'sim_required': SIM_ONLY_DAYS,
        'paper_valid': len(data['paper']),
        'paper_required': PAPER_PHASE_DAYS,
        'sim_dates': data['sim'][-7:],
        'paper_dates': data['paper'][-7:],
    }
