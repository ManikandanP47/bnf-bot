"""
ML Brain — learns from virtual + paper trade outcomes.

Uses scikit-learn (Random Forest) on features logged at entry.
Retrains as shadow/paper trades close. Blends with pattern_memory + RAG.

Neural nets need 100+ samples; this model activates at ML_MIN_SAMPLES (default 25).
"""

import os
import json
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
MODEL_DIR = os.getenv('ML_MODEL_DIR', 'models')
MODEL_FILE = os.path.join(MODEL_DIR, 'win_predictor.joblib')
META_FILE = os.path.join(MODEL_DIR, 'win_predictor_meta.json')
ML_MIN_SAMPLES = int(os.getenv('ML_MIN_SAMPLES', '25'))
ML_ENABLED = os.getenv('ML_LEARNING', 'true').lower() == 'true'

SESSIONS = ('MORNING_TREND', 'AFTERNOON_MOVE', 'OPENING_CHAOS', 'LUNCH', 'EOD')
REGIMES = ('TRENDING', 'RANGING', 'UNKNOWN')


def _conn():
    return sqlite3.connect(DB_FILE)


def _ensure_model_dir():
    os.makedirs(MODEL_DIR, exist_ok=True)


def _parse_hour(entry_time: str) -> int:
    if not entry_time:
        return 10
    try:
        return int(entry_time.split(':')[0])
    except ValueError:
        return 10


def _extract_features_from_shadow(row: dict, entry_json: str = '') -> dict:
    """Build numeric feature dict from shadow_trades + chart snapshot."""
    try:
        entry = json.loads(entry_json or '{}')
    except json.JSONDecodeError:
        entry = {}
    ch = entry.get('chart') or {}

    session = row.get('session') or ch.get('session') or ''
    regime = row.get('regime') or ch.get('regime') or 'UNKNOWN'
    bias = row.get('bias') or 'NEUTRAL'

    feats = {
        'hour': _parse_hour(row.get('entry_time', '')),
        'flow_score': float(row.get('entry_flow_score') or ch.get('flow_score') or 0),
        'score': float(row.get('sim_score') or row.get('score') or 0),
        'rsi_5m': float(ch.get('rsi_5m') or 50),
        'adx': float(ch.get('adx') or 0),
        'entry_prem': float(row.get('entry_prem') or entry.get('premium') or 0),
        'bias_bull': 1.0 if bias == 'BULLISH' else 0.0,
        'choch_5m': 1.0 if ch.get('choch_5m') else 0.0,
        'structure_bull': 1.0 if ch.get('structure_15m') == 'BULLISH' else 0.0,
        'structure_bear': 1.0 if ch.get('structure_15m') == 'BEARISH' else 0.0,
    }
    for s in SESSIONS:
        feats[f'sess_{s}'] = 1.0 if session == s else 0.0
    for r in REGIMES:
        feats[f'reg_{r}'] = 1.0 if regime == r else 0.0
    return feats


def _load_training_rows() -> list:
    """Labeled rows from shadow trades + confirmed paper trades."""
    conn = _conn()
    rows = []

    try:
        shadow = conn.execute("""
            SELECT st.id, st.entry_time, st.session, st.score, st.regime, st.bias,
                   st.entry_prem, st.sim_score, st.entry_flow_score, st.outcome,
                   sc.entry_json
            FROM shadow_trades st
            LEFT JOIN sim_chart_snapshots sc ON sc.shadow_id = st.id
            WHERE st.outcome IN ('WIN', 'LOSS')
            ORDER BY st.id
        """).fetchall()
    except sqlite3.OperationalError:
        try:
            shadow = conn.execute("""
                SELECT id, entry_time, session, score, regime, bias,
                       entry_prem, sim_score, entry_flow_score, outcome, NULL
                FROM shadow_trades
                WHERE outcome IN ('WIN', 'LOSS')
                ORDER BY id
            """).fetchall()
        except sqlite3.OperationalError:
            shadow = []

    for r in shadow:
        row = {
            'entry_time': r[1], 'session': r[2], 'score': r[3], 'regime': r[4],
            'bias': r[5], 'entry_prem': r[6], 'sim_score': r[7],
            'entry_flow_score': r[8], 'outcome': r[9],
        }
        feats = _extract_features_from_shadow(row, r[10] or '')
        feats['label'] = 1 if r[9] == 'WIN' else 0
        rows.append(feats)

    try:
        paper = conn.execute("""
            SELECT t.entry_time, t.session, t.score, t.regime, t.bias,
                   t.entry_prem, t.hour, t.rsi, t.outcome, tf.features_json, tf.adx
            FROM trades t
            LEFT JOIN trade_features tf ON tf.trade_id = t.id
            WHERE t.outcome IN ('WIN', 'LOSS')
            ORDER BY t.id
        """).fetchall()
    except sqlite3.OperationalError:
        paper = []
    conn.close()

    for r in paper:
        extra = {}
        try:
            extra = json.loads(r[9] or '{}')
        except json.JSONDecodeError:
            pass
        row = {
            'entry_time': r[0], 'session': r[1], 'score': r[2], 'regime': r[3],
            'bias': r[4], 'entry_prem': r[5], 'entry_flow_score': extra.get('flow_score', 0),
            'sim_score': r[2],
        }
        ch = {
            'session': r[1], 'regime': r[3], 'rsi_5m': r[7] or 50,
            'flow_score': extra.get('flow_score', 0), 'adx': r[10] or 0,
        }
        feats = _extract_features_from_shadow(row, json.dumps({'chart': ch}))
        feats['hour'] = float(r[6] or _parse_hour(r[0]))
        feats['label'] = 1 if r[8] == 'WIN' else 0
        rows.append(feats)

    return rows


def _feature_names() -> list:
    names = [
        'hour', 'flow_score', 'score', 'rsi_5m', 'adx', 'entry_prem',
        'bias_bull', 'choch_5m', 'structure_bull', 'structure_bear',
    ]
    names += [f'sess_{s}' for s in SESSIONS]
    names += [f'reg_{r}' for r in REGIMES]
    return names


def train_model(force: bool = False) -> dict:
    """
    Train Random Forest on all labeled virtual + paper trades.
    Saves model to models/win_predictor.joblib
    """
    if not ML_ENABLED:
        return {'ok': False, 'reason': 'ML_LEARNING=false'}

    rows = _load_training_rows()
    n = len(rows)
    if n < ML_MIN_SAMPLES and not force:
        return {'ok': False, 'reason': f'need {ML_MIN_SAMPLES} samples, have {n}', 'samples': n}

    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
        import joblib
    except ImportError:
        return {'ok': False, 'reason': 'pip install scikit-learn joblib'}

    names = _feature_names()
    X = np.array([[r.get(k, 0) for k in names] for r in rows], dtype=float)
    y = np.array([r['label'] for r in rows], dtype=int)

    if len(set(y)) < 2:
        return {'ok': False, 'reason': 'need both wins and losses', 'samples': n}

    clf = RandomForestClassifier(
        n_estimators=80,
        max_depth=6,
        min_samples_leaf=2,
        class_weight='balanced',
        random_state=42,
    )
    clf.fit(X, y)

    cv_wr = 0.0
    if n >= 15:
        try:
            scores = cross_val_score(clf, X, y, cv=min(5, n // 3), scoring='accuracy')
            cv_wr = round(float(scores.mean()) * 100, 1)
        except Exception:
            pass

    train_wr = round(float(y.mean()) * 100, 1)
    _ensure_model_dir()
    joblib.dump({'model': clf, 'features': names}, MODEL_FILE)

    importances = sorted(zip(names, clf.feature_importances_), key=lambda x: -x[1])[:3]
    top_features = ', '.join(f'{k}:{v:.2f}' for k, v in importances)

    meta = {
        'trained_at': datetime.now(IST).strftime('%Y-%m-%d %H:%M'),
        'samples': n,
        'wins': int(y.sum()),
        'losses': int(len(y) - y.sum()),
        'train_win_rate': train_wr,
        'cv_accuracy': cv_wr,
        'top_features': top_features,
    }
    with open(META_FILE, 'w') as f:
        json.dump(meta, f)

    return {'ok': True, **meta}


def _load_model():
    if not os.path.exists(MODEL_FILE):
        return None
    try:
        import joblib
        return joblib.load(MODEL_FILE)
    except Exception:
        return None


def _meta() -> dict:
    if not os.path.exists(META_FILE):
        return {}
    try:
        with open(META_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def features_from_signal(signal: dict, params: dict = None) -> dict:
    """Build feature dict for live prediction."""
    from core.shared_state import STATE
    from datetime import datetime
    import pytz

    hour = signal.get('hour')
    if hour is None:
        hour = datetime.now(pytz.timezone('Asia/Kolkata')).hour

    flow = STATE.get('market.flow') or {}
    c15 = STATE.get('market.candles_15m', [])
    adx = 0.0
    try:
        from src.trend_strength import calc_adx
        adx = calc_adx(c15)
    except Exception:
        pass

    row = {
        'entry_time': f'{hour}:00',
        'session': signal.get('session', STATE.get('market.session', '')),
        'score': signal.get('score', 0),
        'sim_score': signal.get('sim_score', signal.get('score', 0)),
        'regime': signal.get('regime', STATE.get('market.regime', '')),
        'bias': signal.get('trend', signal.get('bias', 'NEUTRAL')),
        'entry_prem': (params or {}).get('premium', 0),
        'entry_flow_score': flow.get('flow_score', 0),
    }
    ch = {
        'session': row['session'],
        'regime': row['regime'],
        'rsi_5m': signal.get('rsi_5m', signal.get('rsi', STATE.get('market.rsi_5m', 50))),
        'flow_score': flow.get('flow_score', 0),
        'adx': adx,
        'choch_5m': bool((signal.get('choch') or {}).get('confirmed')),
        'structure_15m': (signal.get('struct_15m') or {}).get('trend', ''),
    }
    return _extract_features_from_shadow(row, json.dumps({'chart': ch}))


def predict_win_probability(signal: dict, params: dict = None) -> dict:
    """
    ML win probability 0–100. ready=False if model not trained yet.
    """
    if not ML_ENABLED:
        return {'ready': False, 'reason': 'disabled'}

    bundle = _load_model()
    meta = _meta()
    if not bundle or not meta.get('samples'):
        return {'ready': False, 'reason': 'not trained', 'samples': meta.get('samples', 0)}

    try:
        import numpy as np
        feats = features_from_signal(signal, params)
        names = bundle['features']
        X = np.array([[feats.get(k, 0) for k in names]], dtype=float)
        clf = bundle['model']
        proba = clf.predict_proba(X)[0]
        win_idx = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
        prob = round(float(proba[win_idx]) * 100, 1)

        importances = sorted(
            zip(names, clf.feature_importances_),
            key=lambda x: -x[1],
        )[:3]
        top = ', '.join(f'{k}:{v:.2f}' for k, v in importances)

        return {
            'ready': True,
            'prob_pct': max(15, min(90, prob)),
            'samples': meta.get('samples', 0),
            'cv_accuracy': meta.get('cv_accuracy', 0),
            'top_features': top,
        }
    except Exception as e:
        return {'ready': False, 'reason': str(e)[:60]}


def maybe_retrain():
    """Called after each virtual/paper close and at EOD."""
    if not ML_ENABLED:
        return
    rows = _load_training_rows()
    n = len(rows)
    meta = _meta()
    last_n = meta.get('samples', 0)
    # Retrain every 5 new samples or first time past threshold
    if n >= ML_MIN_SAMPLES and (n - last_n >= 5 or not meta.get('samples')):
        result = train_model()
        if result.get('ok'):
            print(f"🧠 ML retrained: {result['samples']} samples, CV {result.get('cv_accuracy')}%")
        return result
    return {'ok': False, 'samples': n, 'reason': 'waiting for more data'}


def format_ml_status() -> str:
    meta = _meta()
    rows = _load_training_rows()
    n = len(rows)
    lines = [
        '🤖 *ML Brain* (Random Forest)',
        f"Samples: {n} (min {ML_MIN_SAMPLES} to train)",
    ]
    if meta.get('samples'):
        lines += [
            f"Model: trained {meta.get('trained_at', '?')}",
            f"  Data: {meta.get('wins', 0)}W / {meta.get('losses', 0)}L",
            f"  CV accuracy: {meta.get('cv_accuracy', 0)}%",
        ]
        imp = meta.get('top_features', '')
        if imp:
            lines.append(f"  Key signals: {imp}")
    else:
        lines.append(f"_Collecting virtual trades — {ML_MIN_SAMPLES - n} more needed_")
    lines.append('_Learns from every shadow + paper close automatically_')
    return '\n'.join(lines)


def format_ml_prediction_line(signal: dict, params: dict = None) -> str:
    p = predict_win_probability(signal, params)
    if not p.get('ready'):
        return ''
    return (
        f"🤖 *ML win chance:* {p['prob_pct']}% "
        f"(trained on {p['samples']} trades, CV {p.get('cv_accuracy', 0)}%)"
    )
