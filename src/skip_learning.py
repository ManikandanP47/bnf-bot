"""Skip-learning — was user right to skip? Adjusts caution over time."""

import sqlite3
import os
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')


def analyse_skip_quality(days: int = 7) -> dict:
    conn = sqlite3.connect(DB_FILE)
    since = (datetime.now(IST) - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT would_outcome, would_pnl_rs, score FROM skipped_setups
        WHERE date >= ? AND resolved = 1 AND would_outcome IS NOT NULL
    """, (since,)).fetchall()
    conn.close()

    if not rows:
        return {'n': 0, 'skip_was_right_pct': 0, 'note': ''}

    right = sum(
        1 for r in rows
        if r[0] == 'LOSS' or (r[1] or 0) < 0
    )
    n = len(rows)
    pct = round(right / n * 100, 1)
    note = ''
    min_boost = 0
    if n >= 5 and pct < 40:
        note = (
            f'You skipped {n} setups — only {pct}% would have lost. '
            f'Consider executing more 7+ scores.'
        )
        min_boost = -1
    elif n >= 5 and pct >= 70:
        note = (
            f'Skips were smart ({pct}% would have lost). Keep skipping weak setups.'
        )
        min_boost = 0

    return {
        'n': n,
        'skip_was_right_pct': pct,
        'note': note,
        'min_score_adjust': min_boost,
    }


def format_skip_weekly_section() -> str:
    s = analyse_skip_quality(7)
    if s['n'] < 3:
        return ''
    return (
        f"\n⏭ *Skip learning (7d):* {s['n']} resolved — "
        f"{s['skip_was_right_pct']}% skips were good\n"
        + (f"  {s['note']}" if s['note'] else '')
    )
