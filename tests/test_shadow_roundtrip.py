#!/usr/bin/env python3
"""Shadow virtual trade roundtrip — open → tick → close (temp DB, no Groww)."""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
os.environ['DB_PATH'] = _test_db.name
os.environ['SIM_TELEGRAM_QUIET'] = 'true'
os.environ['ML_MODEL_DIR'] = tempfile.mkdtemp(prefix='bnf_ml_test_')

IST = pytz.timezone('Asia/Kolkata')


def run_roundtrip() -> bool:
    from src.shadow_learning import init_shadow_tables, tick_shadow_trades
    from core.shared_state import STATE

    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    now = datetime.now(IST).strftime('%H:%M')

    conn = sqlite3.connect(_test_db.name)
    conn.execute("""
        INSERT INTO shadow_trades (
            date, entry_time, option_name, bias, session, score, regime,
            bnf_entry, strike, opt_type, expiry, entry_prem, sl_prem, tgt_prem,
            prediction, rag_notes, status, sim_source, mae_prem, mfe_prem,
            peak_pnl_rs, entry_flow_score, prem_source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        today, now, 'BNF 58500 CE', 'BULLISH', 'MORNING_TREND', 6, 'TRENDING',
        58500, 58500, 'CE', '2026-07-02', 140.0, 98.0, 280.0,
        'test prediction', '', 'OPEN', 'TEST', 140.0, 140.0, 0.0, 5, 'GROWW_LTP',
    ))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    STATE.set('market.price', 58600)
    STATE.set('market.flow', {'flow_score': 5})

    def fake_mtm(pos, price):
        return {
            'premium': 285.0,
            'pnl_rs': round((285 - pos['entry_price']) * 15, 0),
            'prem_source': 'GROWW_LTP',
            'is_real': True,
            'symbol': 'TEST',
        }

    with patch('src.position_watch.smart_mark_to_market', side_effect=fake_mtm):
        tick_shadow_trades()

    conn = sqlite3.connect(_test_db.name)
    row = conn.execute(
        "SELECT status, outcome, pnl_rs FROM shadow_trades WHERE id=?", (sid,)
    ).fetchone()
    conn.close()

    if not row:
        print('FAIL: shadow row missing')
        return False
    status, outcome, pnl = row
    if status != 'CLOSED':
        print(f'FAIL: expected CLOSED, got {status}')
        return False
    if outcome != 'WIN':
        print(f'FAIL: expected WIN, got {outcome}')
        return False
    if pnl <= 0:
        print(f'FAIL: expected positive pnl, got {pnl}')
        return False
    print(f'PASS: shadow #{sid} {status} {outcome} ₹{pnl}')
    return True


if __name__ == '__main__':
    ok = run_roundtrip()
    sys.exit(0 if ok else 1)
