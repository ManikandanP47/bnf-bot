"""
Dashboard API — single JSON snapshot for web UI and external tools.
"""

import json
import os
from datetime import datetime
from collections import Counter
import pytz

IST = pytz.timezone('Asia/Kolkata')
EVIDENCE_FILE = os.getenv('SIM_EVIDENCE_FILE', 'sim_evidence.jsonl')


def _recent_evidence(limit: int = 25) -> list:
    if not os.path.exists(EVIDENCE_FILE):
        return []
    today = datetime.now(IST).strftime('%Y-%m-%d')
    rows = []
    try:
        with open(EVIDENCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get('date') == today:
                        rows.append(d)
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return rows[-limit:]


def build_market_payload() -> dict:
    from core.shared_state import STATE

    zone = STATE.get('zone', {}) or {}
    flow = STATE.get('market.flow') or STATE.get('signals.market_flow') or {}
    price = STATE.get('market.price', 0)
    vwap = STATE.get('market.vwap', 0)
    return {
        'price': price,
        'vwap': vwap,
        'above_vwap': price > vwap if price and vwap else None,
        'session': STATE.get('market.session', 'CLOSED'),
        'regime': STATE.get('market.regime', ''),
        'rsi_5m': STATE.get('market.rsi_5m', 0),
        'rsi_1m': STATE.get('market.rsi_1m', 0),
        'data_source': STATE.get('market.data_source', ''),
        'flow_score': flow.get('flow_score', 0),
        'flow': {
            'vix': (flow.get('vix') or {}).get('value'),
            'pcr': (flow.get('oi') or {}).get('pcr'),
            'ema': (flow.get('ema') or {}).get('status'),
            'oi_bias': (flow.get('oi') or {}).get('bias'),
        },
        'zone': {
            'active': zone.get('active', False),
            'bias': zone.get('bias', ''),
            'low': zone.get('low') or zone.get('zone_low'),
            'high': zone.get('high') or zone.get('zone_high'),
            'option': zone.get('name') or zone.get('option_name', ''),
        },
        'market_open': STATE.get('system.market_open', False),
        'paused': STATE.get('system.paused', False),
        'updated_at': STATE.get('market.updated_at', ''),
    }


def build_training_payload() -> dict:
    from src.shadow_learning import learning_phase_info, get_today_shadow_trades
    from src.valid_training_days import get_valid_day_counts, evaluate_day
    from src.sim_evidence import get_daily_counts, is_training_day_valid

    info = learning_phase_info()
    valid = get_valid_day_counts()
    today = evaluate_day()
    audit = is_training_day_valid()
    trades = get_today_shadow_trades()

    return {
        'phase': info['phase'],
        'elapsed_calendar_days': info['elapsed_days'],
        'valid_sim_days': valid['sim_valid'],
        'valid_sim_required': valid['sim_required'],
        'valid_paper_days': valid['paper_valid'],
        'valid_paper_required': valid['paper_required'],
        'days_until_paper': info['days_until_paper'],
        'days_until_live': info['days_until_live'],
        'today_valid': today['sim_valid'] or today['paper_valid'],
        'today_evaluation': today,
        'audit_valid': audit['valid'],
        'shadow_today': len(trades),
        'shadow_trades': trades,
        'counts': get_daily_counts(),
    }


def build_ml_payload() -> dict:
    try:
        from src.ml_brain import _meta, _load_training_rows, ML_MIN_SAMPLES, ML_NN_MIN_SAMPLES
        meta = _meta()
        n = len(_load_training_rows())
        return {
            'samples': n,
            'rf_min': ML_MIN_SAMPLES,
            'nn_min': ML_NN_MIN_SAMPLES,
            'meta': meta,
            'active': meta.get('active', 'none'),
            'cv_accuracy': meta.get('cv_accuracy', 0),
        }
    except Exception as e:
        return {'error': str(e)[:80], 'samples': 0}


def build_readiness_payload() -> dict:
    try:
        from src.brain_metrics import assess_live_readiness
        return assess_live_readiness()
    except Exception as e:
        return {'ready': False, 'reason': str(e), 'gates': []}


def build_scans_payload() -> dict:
    try:
        from src.sim_scan_journal import get_today_scans
        scans = get_today_scans()
        real = [s for s in scans if s.get('event') != 'COOLDOWN']
        reasons = Counter(s.get('reason', '') for s in real if s.get('event') == 'SKIP')
        return {
            'total': len(real),
            'opens': sum(1 for s in real if s.get('event') == 'OPEN'),
            'skips': sum(1 for s in real if s.get('event') == 'SKIP'),
            'skip_reasons': dict(reasons.most_common(10)),
            'recent': real[-15:],
        }
    except Exception:
        return {'total': 0, 'recent': [], 'skip_reasons': {}}


def build_agents_payload() -> dict:
    from core.shared_state import STATE
    return {
        'agents': STATE.get('system.agent_status', {}),
        'errors': STATE.get('system.errors', [])[-10:],
        'running': STATE.get('system.running', False),
    }


def build_dashboard_payload() -> dict:
    from src.intelligence_brief import build_intelligence_brief
    from src.db_persistence import get_table_counts, format_persistence_line

    now = datetime.now(IST)
    payload = {
        'ts': now.isoformat(),
        'ts_display': now.strftime('%d %b %Y %I:%M:%S %p IST'),
        'market': build_market_payload(),
        'training': build_training_payload(),
        'ml': build_ml_payload(),
        'readiness': build_readiness_payload(),
        'scans': build_scans_payload(),
        'agents': build_agents_payload(),
        'persistence': get_table_counts(),
        'persistence_line': format_persistence_line(),
        'intelligence': build_intelligence_brief(),
        'evidence_tail': _recent_evidence(20),
    }
    try:
        from core.shared_state import STATE
        from src.groww_health import _cache_age_sec
        payload['groww'] = {
            'data_source': STATE.get('market.data_source', ''),
            'token_cache_age_sec': _cache_age_sec(),
            'feed': STATE.get('system.groww_feed', {}),
        }
    except Exception:
        payload['groww'] = {}
    try:
        from core.shared_state import STATE
        payload['backtest'] = STATE.get('system.backtest_summary') or {}
    except Exception:
        payload['backtest'] = {}
    return payload
