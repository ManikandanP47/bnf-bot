"""Feature store — snapshot market context at every trade entry."""

import json
import sqlite3
import os

DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')


def init_feature_table(conn=None):
    c = conn or sqlite3.connect(DB_FILE)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_features (
            trade_id     INTEGER PRIMARY KEY,
            features_json TEXT,
            prob_pct     REAL,
            flow_score   INTEGER,
            adx          REAL,
            max_pain     REAL,
            vix          REAL
        )
    """)
    if not conn:
        c.commit()
        c.close()


def capture_entry_features(trade_id: int, signal: dict, params: dict = None):
    """Log features at trade entry for later analysis."""
    from core.shared_state import STATE
    from src.trade_probability import estimate_win_probability
    from src.trend_strength import calc_adx

    flow = STATE.get('signals.market_flow') or STATE.get('market.flow') or {}
    oi = flow.get('oi') or STATE.get('market.oi_deep') or {}
    vix = (flow.get('vix') or {}).get('vix', 0)
    c15 = STATE.get('market.candles_15m', [])
    adx = calc_adx(c15)
    prob = estimate_win_probability(signal)

    feats = {
        'score': signal.get('score'),
        'session': signal.get('session'),
        'regime': signal.get('regime'),
        'trend': signal.get('trend'),
        'flow_score': flow.get('flow_score', 0),
        'vix': vix,
        'pcr': oi.get('pcr'),
        'max_pain': oi.get('max_pain'),
        'premium': (params or {}).get('premium'),
        'data_source': STATE.get('market.data_source'),
    }

    try:
        init_feature_table()
        conn = sqlite3.connect(DB_FILE)
        conn.execute("""
            INSERT OR REPLACE INTO trade_features
            (trade_id, features_json, prob_pct, flow_score, adx, max_pain, vix)
            VALUES (?,?,?,?,?,?,?)
        """, (
            trade_id,
            json.dumps(feats),
            prob['prob_pct'],
            flow.get('flow_score', 0),
            adx,
            oi.get('max_pain', 0) or 0,
            vix,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass
