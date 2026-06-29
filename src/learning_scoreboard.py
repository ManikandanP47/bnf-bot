"""
Shadow vs paper scoreboard + slippage hints from shadow MAE.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')


def _conn():
    return sqlite3.connect(DB_FILE)


def shadow_vs_paper_stats(days: int = 7) -> dict:
    since = (datetime.now(IST) - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = _conn()
    paper = conn.execute("""
        SELECT outcome, pnl_rs FROM trades
        WHERE date >= ? AND outcome IS NOT NULL
    """, (since,)).fetchall()
    shadow = conn.execute("""
        SELECT outcome, pnl_rs FROM shadow_trades
        WHERE date >= ? AND outcome IS NOT NULL
    """, (since,)).fetchall()
    conn.close()

    def _wr(rows):
        if not rows:
            return 0, 0, 0.0
        wins = sum(1 for r in rows if r[0] == 'WIN')
        pnl = sum(r[1] or 0 for r in rows)
        return len(rows), wins, round(wins / len(rows) * 100, 1)

    pn, pw, pwr = _wr(paper)
    sn, sw, swr = _wr(shadow)
    return {
        'days': days,
        'paper_n': pn, 'paper_wins': pw, 'paper_wr': pwr,
        'paper_pnl': round(sum(r[1] or 0 for r in paper), 0),
        'shadow_n': sn, 'shadow_wins': sw, 'shadow_wr': swr,
        'shadow_pnl': round(sum(r[1] or 0 for r in shadow), 0),
    }


def format_scoreboard_block(days: int = 7) -> str:
    s = shadow_vs_paper_stats(days)
    return (
        f"📊 *Shadow vs Paper ({s['days']}d)*\n"
        f"  Shadow: {s['shadow_n']} drills | {s['shadow_wr']}% WR | ₹{s['shadow_pnl']:,} virtual\n"
        f"  Paper:  {s['paper_n']} trades | {s['paper_wr']}% WR | ₹{s['paper_pnl']:,}\n"
        f"  _Shadow = bot gym; Paper = your Execute trades_"
    )


def estimate_slippage_from_shadow() -> dict:
    """Suggest paper slippage from avg shadow premium drift."""
    conn = _conn()
    rows = conn.execute("""
        SELECT entry_prem, exit_prem, pnl_rs FROM shadow_trades
        WHERE outcome IS NOT NULL AND entry_prem > 0
        ORDER BY id DESC LIMIT 30
    """).fetchall()
    conn.close()
    if len(rows) < 5:
        return {'available': False}
    avg_prem = sum(r[0] for r in rows) / len(rows)
    suggested = max(5, min(15, round(avg_prem * 0.03)))
    return {
        'available': True,
        'samples': len(rows),
        'suggested_per_unit': suggested,
        'note': f'Avg shadow entry ₹{avg_prem:.0f} → suggest paper slippage ₹{suggested}/unit',
    }


def post_learning_max_trades() -> int:
    """After learning phase: precision mode trade cap."""
    from src.shadow_learning import is_learning_phase
    if is_learning_phase():
        return int(os.getenv('LEARNING_MAX_TRADES_DAY', '1'))
    return int(os.getenv('POST_LEARNING_MAX_TRADES_DAY', '1'))
