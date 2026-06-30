"""
ML Brain — learns from virtual + paper trade outcomes.

Phase 1 (25+ samples): Random Forest
Phase 2 (100+ samples): Neural net (MLP) auto-activates; predictions blend both.

Retrains as shadow/paper trades close. Blends with pattern_memory + RAG.
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
ML_NN_MIN_SAMPLES = int(os.getenv('ML_NN_MIN_SAMPLES', '100'))
ML_NN_ENABLED = os.getenv('ML_NN_ENABLED', 'true').lower() == 'true'
ML_ENABLED = os.getenv('ML_LEARNING', 'true').lower() == 'true'

SESSIONS = ('MORNING_TREND', 'AFTERNOON_MOVE', 'OPENING_CHAOS', 'LUNCH', 'EOD')
REGIMES = ('TRENDING', 'RANGING', 'UNKNOWN')


def _conn():
    from src.db_persistence import connect
    return connect()


def labeled_sample_count() -> int:
    return len(_load_training_rows())


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

    gk = entry.get('greeks') or {}
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
        'delta': float(gk.get('delta', 0)),
        'gamma': float(gk.get('gamma', 0)),
        'theta_lot': float(gk.get('theta_per_lot_day', 0)),
        'vega_lot': float(gk.get('vega_per_lot_1pct', 0)),
        'iv_pct': float(gk.get('iv_pct', 0)),
        'iv_rank': float(gk.get('iv_rank', 50)),
        'dte': float(gk.get('dte_days', 0)),
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


def _build_xy(rows: list):
    import numpy as np
    names = _feature_names()
    X = np.array([[r.get(k, 0) for k in names] for r in rows], dtype=float)
    y = np.array([r['label'] for r in rows], dtype=int)
    return names, X, y


def _cv_accuracy(clf, X, y, n: int) -> float:
    if n < 15:
        return 0.0
    try:
        from sklearn.model_selection import cross_val_score
        scores = cross_val_score(clf, X, y, cv=min(5, max(2, n // 5)), scoring='accuracy')
        return round(float(scores.mean()) * 100, 1)
    except Exception:
        return 0.0


def _train_rf(X, y, n: int) -> dict:
    from sklearn.ensemble import RandomForestClassifier
    clf = RandomForestClassifier(
        n_estimators=80,
        max_depth=6,
        min_samples_leaf=2,
        class_weight='balanced',
        random_state=42,
    )
    clf.fit(X, y)
    cv = _cv_accuracy(clf, X, y, n)
    imp = sorted(zip(_feature_names(), clf.feature_importances_), key=lambda x: -x[1])[:3]
    return {
        'model': clf,
        'cv_accuracy': cv,
        'top_features': ', '.join(f'{k}:{v:.2f}' for k, v in imp),
    }


def _train_nn(X, y, n: int) -> dict:
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    mlp = Pipeline([
        ('scaler', StandardScaler()),
        ('mlp', MLPClassifier(
            hidden_layer_sizes=(32, 16),
            activation='relu',
            max_iter=800,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=20,
            random_state=42,
        )),
    ])
    mlp.fit(X, y)
    cv = _cv_accuracy(mlp, X, y, n)
    return {'model': mlp, 'cv_accuracy': cv, 'top_features': 'neural net (32→16)'}


def _proba_pct(clf, X) -> float:
    proba = clf.predict_proba(X)[0]
    win_idx = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
    return round(float(proba[win_idx]) * 100, 1)


def train_model(force: bool = False) -> dict:
    """
    Train RF at 25+ samples; auto-add neural net (MLP) at 100+ samples.
    """
    if not ML_ENABLED:
        return {'ok': False, 'reason': 'ML_LEARNING=false'}

    rows = _load_training_rows()
    n = len(rows)
    if n < ML_MIN_SAMPLES and not force:
        return {'ok': False, 'reason': f'need {ML_MIN_SAMPLES} samples, have {n}', 'samples': n}

    try:
        import joblib
    except ImportError:
        return {'ok': False, 'reason': 'pip install scikit-learn joblib'}

    names, X, y = _build_xy(rows)
    if len(set(y)) < 2:
        return {'ok': False, 'reason': 'need both wins and losses', 'samples': n}

    rf_info = _train_rf(X, y, n)
    bundle = {'features': names, 'rf': rf_info['model'], 'nn': None}

    nn_info = None
    nn_active = False
    if ML_NN_ENABLED and n >= ML_NN_MIN_SAMPLES:
        try:
            nn_info = _train_nn(X, y, n)
            bundle['nn'] = nn_info['model']
            nn_active = True
        except Exception as e:
            nn_info = {'error': str(e)[:80]}

    active = 'ensemble' if nn_active else 'rf'
    bundle['active'] = active
    _ensure_model_dir()
    joblib.dump(bundle, MODEL_FILE)

    train_wr = round(float(y.mean()) * 100, 1)
    meta = {
        'trained_at': datetime.now(IST).strftime('%Y-%m-%d %H:%M'),
        'samples': n,
        'wins': int(y.sum()),
        'losses': int(len(y) - y.sum()),
        'train_win_rate': train_wr,
        'active': active,
        'rf': {
            'cv_accuracy': rf_info['cv_accuracy'],
            'top_features': rf_info['top_features'],
        },
        'nn': None,
        # legacy fields for older readers
        'cv_accuracy': rf_info['cv_accuracy'],
        'top_features': rf_info['top_features'],
    }
    if nn_info and nn_info.get('cv_accuracy') is not None:
        meta['nn'] = {
            'cv_accuracy': nn_info['cv_accuracy'],
            'top_features': nn_info.get('top_features', ''),
            'min_samples': ML_NN_MIN_SAMPLES,
        }
        meta['cv_accuracy'] = round(
            (rf_info['cv_accuracy'] * 0.4 + nn_info['cv_accuracy'] * 0.6), 1
        )

    with open(META_FILE, 'w') as f:
        json.dump(meta, f)

    try:
        import shutil
        archive = os.path.join(MODEL_DIR, 'archive')
        os.makedirs(archive, exist_ok=True)
        stamp = datetime.now(IST).strftime('%Y%m%d_%H%M')
        shutil.copy2(MODEL_FILE, os.path.join(archive, f'win_predictor_{stamp}.joblib'))
        shutil.copy2(META_FILE, os.path.join(archive, f'win_predictor_{stamp}_meta.json'))
    except Exception:
        pass

    msg = f"RF CV {rf_info['cv_accuracy']}%"
    if nn_active:
        msg += f" | NN CV {nn_info['cv_accuracy']}% (ensemble active)"
    return {'ok': True, 'samples': n, 'active': active, 'message': msg, **meta}


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
    """ML win probability 0–100. Uses RF; blends with NN when 100+ samples trained."""
    if not ML_ENABLED:
        return {'ready': False, 'reason': 'disabled'}

    bundle = _load_model()
    meta = _meta()
    if not bundle or not meta.get('samples'):
        rows = _load_training_rows()
        return {
            'ready': False,
            'reason': 'not trained',
            'samples': len(rows),
            'nn_in': max(0, ML_NN_MIN_SAMPLES - len(rows)),
        }

    try:
        import numpy as np
        feats = features_from_signal(signal, params)
        names = bundle['features']
        X = np.array([[feats.get(k, 0) for k in names]], dtype=float)

        rf = bundle.get('rf') or bundle.get('model')
        nn = bundle.get('nn')
        active = bundle.get('active', 'rf')

        if rf is None:
            return {'ready': False, 'reason': 'no model'}

        rf_prob = _proba_pct(rf, X)
        nn_prob = None
        if nn is not None:
            nn_prob = _proba_pct(nn, X)

        if nn_prob is not None and active == 'ensemble':
            prob = round(rf_prob * 0.35 + nn_prob * 0.65, 1)
            model_label = 'RF+NN ensemble'
        else:
            prob = rf_prob
            model_label = 'Random Forest'

        rf_meta = meta.get('rf') or {}
        nn_meta = meta.get('nn') or {}

        return {
            'ready': True,
            'prob_pct': max(15, min(90, prob)),
            'samples': meta.get('samples', 0),
            'cv_accuracy': meta.get('cv_accuracy', 0),
            'model': model_label,
            'rf_prob': rf_prob,
            'nn_prob': nn_prob,
            'top_features': rf_meta.get('top_features', meta.get('top_features', '')),
            'nn_active': nn_prob is not None,
            'nn_cv': nn_meta.get('cv_accuracy', 0),
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
    crossed_nn = last_n < ML_NN_MIN_SAMPLES <= n
    if n >= ML_MIN_SAMPLES and (n - last_n >= 5 or not meta.get('samples') or crossed_nn):
        result = train_model()
        if result.get('ok'):
            print(f"🧠 ML retrained ({result.get('active')}): {result['samples']} samples — {result.get('message', '')}")
        return result
    return {'ok': False, 'samples': n, 'reason': 'waiting for more data'}


def _samples_eta(n: int) -> str:
    """Rough trading sessions until RF/NN from recent virtual close rate."""
    meta = _meta()
    if n >= ML_NN_MIN_SAMPLES and meta.get('nn'):
        return ''
    try:
        conn = _conn()
        rows = conn.execute("""
            SELECT date, COUNT(*) FROM shadow_trades
            WHERE status='CLOSED'
            GROUP BY date ORDER BY date DESC LIMIT 14
        """).fetchall()
        conn.close()
        if not rows:
            return '_Virtual sims close in market hours — watch /shadow_'
        total = sum(r[1] for r in rows)
        days = len(rows)
        rate = total / days
        if rate < 0.3:
            return '_Low close rate — quiet market or bot recently started_'
        parts = []
        if n < ML_MIN_SAMPLES:
            parts.append(f'RF ~{max(1, int((ML_MIN_SAMPLES - n) / rate + 0.5))} sessions')
        if n < ML_NN_MIN_SAMPLES:
            parts.append(f'NN ~{max(1, int((ML_NN_MIN_SAMPLES - n) / rate + 0.5))} sessions')
        return f"_~{rate:.1f} closes/day → {' | '.join(parts)}_"
    except Exception:
        return ''


def format_ml_status() -> str:
    meta = _meta()
    rows = _load_training_rows()
    n = len(rows)
    nn_left = max(0, ML_NN_MIN_SAMPLES - n)

    lines = [
        '🤖 *ML Brain*',
        f"Samples: {n} (RF at {ML_MIN_SAMPLES}+ | NN at {ML_NN_MIN_SAMPLES}+)",
    ]

    if meta.get('samples'):
        active = meta.get('active', 'rf')
        lines.append(f"Active: *{active}* — trained {meta.get('trained_at', '?')}")
        lines.append(f"  Data: {meta.get('wins', 0)}W / {meta.get('losses', 0)}L")

        rf = meta.get('rf') or {}
        lines.append(f"  🌲 RF CV: {rf.get('cv_accuracy', meta.get('cv_accuracy', 0))}%")
        if rf.get('top_features'):
            lines.append(f"     {rf['top_features']}")

        nn = meta.get('nn')
        if nn:
            lines.append(f"  🧠 NN CV: {nn.get('cv_accuracy', 0)}% (ensemble ON)")
        elif nn_left > 0:
            lines.append(f"  🧠 NN: {nn_left} more samples to auto-activate")
        else:
            lines.append('  🧠 NN: training on next retrain')
    else:
        lines.append(f"_RF needs {max(0, ML_MIN_SAMPLES - n)} more | NN at {ML_NN_MIN_SAMPLES}_")

    eta = _samples_eta(n)
    if eta:
        lines.append(eta)

    lines.append('_Learns from every virtual + paper close automatically_')
    return '\n'.join(lines)


def format_ml_prediction_line(signal: dict, params: dict = None) -> str:
    p = predict_win_probability(signal, params)
    if not p.get('ready'):
        return ''
    model = p.get('model', 'ML')
    extra = ''
    if p.get('nn_active'):
        extra = f" | RF {p.get('rf_prob')}% + NN {p.get('nn_prob')}%"
    return (
        f"🤖 *{model}:* {p['prob_pct']}% win chance "
        f"({p['samples']} trades, CV {p.get('cv_accuracy', 0)}%{extra})"
    )
