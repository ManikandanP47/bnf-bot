#!/usr/bin/env python3
"""ML neural net auto-activation — trains ensemble at 100+ synthetic samples."""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime

import pytz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_model_dir = tempfile.mkdtemp(prefix='bnf_ml_nn_')
os.environ['DB_PATH'] = _test_db.name
os.environ['ML_MODEL_DIR'] = _model_dir
os.environ['ML_MIN_SAMPLES'] = '25'
os.environ['ML_NN_MIN_SAMPLES'] = '100'
os.environ['ML_NN_ENABLED'] = 'true'

IST = pytz.timezone('Asia/Kolkata')


def seed_shadow_trades(n: int = 110):
    from src.shadow_learning import init_shadow_tables
    init_shadow_tables()
    conn = sqlite3.connect(_test_db.name)
    today = datetime.now(IST).strftime('%Y-%m-%d')
    for i in range(n):
        win = i % 3 != 0
        conn.execute("""
            INSERT INTO shadow_trades (
                date, entry_time, option_name, bias, session, score, regime,
                bnf_entry, strike, opt_type, expiry, entry_prem, sl_prem, tgt_prem,
                prediction, status, outcome, pnl_rs, sim_score, entry_flow_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            today, '10:00', f'BNF 58500 CE', 'BULLISH', 'MORNING_TREND', 6, 'TRENDING',
            58500, 58500, 'CE', '2026-07-02', 140, 98, 280,
            'test', 'CLOSED', 'WIN' if win else 'LOSS', 200 if win else -150, 6, 5 + (i % 4),
        ))
    conn.commit()
    conn.close()


def main() -> bool:
    seed_shadow_trades(110)
    from src.ml_brain import train_model, predict_win_probability, format_ml_status

    result = train_model(force=True)
    if not result.get('ok'):
        print('FAIL train:', result)
        return False
    if result.get('active') != 'ensemble':
        print('FAIL expected ensemble, got', result.get('active'))
        return False

    pred = predict_win_probability({
        'session': 'MORNING_TREND', 'score': 7, 'regime': 'TRENDING',
        'trend': 'BULLISH', 'hour': 10,
    })
    if not pred.get('ready') or not pred.get('nn_active'):
        print('FAIL predict:', pred)
        return False

    print('PASS: ensemble trained, nn_active=True, prob=', pred.get('prob_pct'))
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
