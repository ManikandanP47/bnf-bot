"""Telegram notifications for virtual sim — quiet mode during heavy training days."""

import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
SIM_TELEGRAM_QUIET = os.getenv('SIM_TELEGRAM_QUIET', 'true').lower() == 'true'
SIM_TELEGRAM_CLOSE_MIN_PNL = float(os.getenv('SIM_TELEGRAM_CLOSE_MIN_PNL', '150'))
SIM_TELEGRAM_MAX_PER_DAY = int(os.getenv('SIM_TELEGRAM_MAX_PER_DAY', '5'))


def _conn():
    return sqlite3.connect(DB_FILE)


def _notify_count_today() -> int:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    try:
        row = _conn().execute(
            "SELECT value FROM bot_flags WHERE key=?",
            (f'sim_tg_count_{today}',),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _bump_notify_count():
    today = datetime.now(IST).strftime('%Y-%m-%d')
    key = f'sim_tg_count_{today}'
    try:
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_flags (key TEXT PRIMARY KEY, value TEXT)
        """)
        n = _notify_count_today() + 1
        conn.execute(
            "INSERT OR REPLACE INTO bot_flags (key, value) VALUES (?, ?)",
            (key, str(n)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def should_notify_sim_open() -> bool:
    if not SIM_TELEGRAM_QUIET:
        return True
    return False


def should_notify_sim_close(pnl_rs: float, outcome: str) -> bool:
    if not SIM_TELEGRAM_QUIET:
        return True
    if _notify_count_today() >= SIM_TELEGRAM_MAX_PER_DAY:
        return False
    if outcome == 'WIN' and pnl_rs >= SIM_TELEGRAM_CLOSE_MIN_PNL:
        return True
    if outcome == 'LOSS' and abs(pnl_rs) >= SIM_TELEGRAM_CLOSE_MIN_PNL:
        return True
    return False


def notify_sim_telegram(msg: str, kind: str = 'close', pnl_rs: float = 0, outcome: str = ''):
    """Send sim Telegram only when quiet rules allow."""
    if kind == 'open' and not should_notify_sim_open():
        return
    if kind == 'close' and not should_notify_sim_close(pnl_rs, outcome):
        return
    try:
        from core.messenger import Messenger
        if Messenger().send(msg):
            _bump_notify_count()
    except Exception:
        pass


def format_quiet_mode_line() -> str:
    if not SIM_TELEGRAM_QUIET:
        return 'Sim Telegram: every open/close'
    return (
        f'Sim Telegram: quiet (closes ≥₹{SIM_TELEGRAM_CLOSE_MIN_PNL:.0f}, '
        f'max {SIM_TELEGRAM_MAX_PER_DAY}/day) — use /simreport for full digest'
    )
