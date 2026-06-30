"""
Active live learning — sim observations, market observer, intraday insights.

Called by LearningAgent every ~60s during market hours (no extra Groww API).
"""

import json
import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
LIVE_LEARNING = os.getenv('LIVE_LEARNING', 'true').lower() == 'true'
OBSERVER_INTERVAL_SEC = int(os.getenv('OBSERVER_INTERVAL_SEC', '900'))


def _conn():
    from src.db_persistence import connect
    return connect()


def _last_obs_id() -> int:
    from core.shared_state import STATE
    return int(STATE.get('brain.last_obs_id', 0) or 0)


def _set_last_obs_id(oid: int):
    from core.shared_state import STATE
    STATE.set('brain.last_obs_id', oid)


def ingest_sim_observations(brain) -> dict:
    """Turn new sim_learning_log rows into pattern_memory observe keys."""
    last_id = _last_obs_id()
    conn = _conn()
    rows = conn.execute("""
        SELECT id, session, event, lesson, metrics_json, sim_score
        FROM sim_learning_log WHERE id > ? ORDER BY id
    """, (last_id,)).fetchall()
    conn.close()

    if not rows:
        return {'new': 0}

    today = datetime.now(IST).strftime('%Y-%m-%d')
    ingested = 0
    skip_by_session = {}
    open_by_session = {}

    for oid, session, event, lesson, mj, score in rows:
        _set_last_obs_id(oid)
        ingested += 1
        sess = session or 'UNKNOWN'
        if event == 'SKIP':
            skip_by_session[sess] = skip_by_session.get(sess, 0) + 1
            reason = 'chop' if 'chop' in (lesson or '').lower() else 'low_score'
            if 'expiry' in (lesson or '').lower():
                reason = 'expiry'
            if 'flow weak' in (lesson or '').lower():
                reason = 'weak_flow'
            key = f"observe:skip:{sess}:{reason}"
            brain._record_observe_key(key, good_avoid=1, today=today)
        elif event == 'OPEN':
            open_by_session[sess] = open_by_session.get(sess, 0) + 1
            try:
                metrics = json.loads(mj or '{}')
            except json.JSONDecodeError:
                metrics = {}
            regime = metrics.get('regime', 'UNKNOWN')
            key = f"observe:open:{sess}:{regime}"
            brain._record_observe_key(key, good_avoid=0, today=today)

    return {
        'new': ingested,
        'skips': skip_by_session,
        'opens': open_by_session,
    }


def run_market_observer() -> dict:
    """Opening range + regime snapshot (yfinance — no Groww)."""
    try:
        from src.api_scheduler import should_fetch, mark_fetched
        if not should_fetch('market_observer', OBSERVER_INTERVAL_SEC):
            from core.shared_state import STATE
            return STATE.get('market.observer') or {}
    except Exception:
        pass

    try:
        from src.market_observer import observe_market
        obs = observe_market()
        if obs.get('session'):
            from core.shared_state import STATE
            STATE.set('market.observer', obs)
            try:
                from src.api_scheduler import mark_fetched
                mark_fetched('market_observer')
            except Exception:
                pass
        return obs
    except Exception as e:
        return {'error': str(e)[:60]}


def build_live_insights(brain) -> dict:
    """Intraday summary pushed to STATE for risk/analysis."""
    from core.shared_state import STATE

    obs = STATE.get('market.observer') or {}
    regime = (obs.get('regime') or {}).get('regime', '')
    or_sig = (obs.get('or') or {}).get('signal', '')
    chain = STATE.get('market.option_chain') or {}

    conn = _conn()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    scan_n = conn.execute(
        "SELECT COUNT(*) FROM sim_learning_log WHERE date=?", (today,)
    ).fetchone()[0]
    open_n = conn.execute(
        "SELECT COUNT(*) FROM sim_learning_log WHERE date=? AND event='OPEN'", (today,)
    ).fetchone()[0]
    conn.close()

    chop_skip = brain.get_pattern_stats('observe:skip:LUNCH_CHOP:chop', min_samples=3)
    insights = {
        'scans_today': scan_n,
        'virtual_opens_today': open_n,
        'regime': regime,
        'or_signal': or_sig,
        'iv_rank': chain.get('iv_rank'),
        'session_quality': (obs.get('session') or {}).get('quality', ''),
        'tradeable_obs': (obs.get('overall') or {}).get('tradeable'),
        'updated': datetime.now(IST).strftime('%H:%M:%S'),
    }
    if chop_skip and chop_skip.get('samples', 0) >= 5:
        insights['lunch_chop_skips'] = chop_skip.get('samples', 0)

    return insights


def run_active_learning_cycle(brain) -> dict:
    """One intraday learning tick."""
    if not LIVE_LEARNING:
        return {}
    if not __import__('core.shared_state', fromlist=['STATE']).STATE.get('system.market_open'):
        return {}

    obs_result = run_market_observer()
    sim_result = ingest_sim_observations(brain)
    insights = build_live_insights(brain)

    from core.shared_state import STATE
    STATE.set('brain.live_insights', insights)

    return {'observer': bool(obs_result.get('session')), 'sim': sim_result, 'insights': insights}


def format_observer_telegram() -> str:
    """Morning market context for Telegram."""
    from src.market_observer import get_market_summary
    return get_market_summary()
