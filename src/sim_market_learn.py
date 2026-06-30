"""
Sim market learning — observes and records on EVERY scan, even when no trade opens.

Uses in-memory STATE only (no extra Groww API calls). Builds the intraday
pattern library: session, flow, OI, theta, S/R, zone, structure — win or skip.
"""

import json
import os
from datetime import datetime
import pytz

from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
SIM_LEARNING_LOG = os.getenv('SIM_LEARNING_LOG', 'true').lower() == 'true'


def _conn():
    from src.db_persistence import connect
    return connect()


def init_learning_log_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sim_learning_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT,
            time        TEXT,
            event       TEXT,
            price       REAL,
            session     TEXT,
            bias        TEXT,
            sim_score   INTEGER,
            lesson      TEXT,
            metrics_json TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_learn_date ON sim_learning_log(date, time)"
    )
    conn.commit()
    conn.close()


def _structure_hint(c15m: list) -> str:
    if len(c15m) < 10:
        return 'warming'
    try:
        from agents.analysis_agent import get_structure
        s = get_structure(c15m)
        return s.get('trend', 'NEUTRAL') or 'NEUTRAL'
    except Exception:
        return 'unknown'


def _zone_distance_pct(price: float, zone: dict) -> float:
    if not zone.get('active') or not price:
        return -1
    low, high = float(zone.get('low', 0) or 0), float(zone.get('high', 0) or 0)
    if not low or not high:
        return -1
    mid = (low + high) / 2
    return round(abs(price - mid) / mid * 100, 2)


def build_learning_snapshot(scan_result: dict) -> dict:
    """Rich observation from cached STATE — zero extra API calls."""
    zone = STATE.get('zone', {}) or {}
    flow = STATE.get('market.flow') or STATE.get('signals.market_flow') or {}
    ctx = STATE.get('market.context') or {}
    price = float(STATE.get('market.price', 0) or 0)
    c5m = STATE.get('market.candles_5m', []) or []
    c15m = STATE.get('market.candles_15m', []) or []
    session = STATE.get('market.session', 'CLOSED')
    regime = STATE.get('market.regime', '')
    rsi = float(STATE.get('market.rsi_5m', 50) or 50)

    oi = flow.get('oi') or {}
    vix = (flow.get('vix') or {}).get('value') or ctx.get('vix', {}).get('value')
    pcr = oi.get('pcr')
    max_pain = oi.get('max_pain') or ctx.get('max_pain')
    theta = flow.get('theta') or {}
    chart = flow.get('chart') or {}

    struct = _structure_hint(c15m)
    zone_dist = _zone_distance_pct(price, zone)
    fs = int(flow.get('flow_score', 0) or 0)
    sim_score = int(scan_result.get('sim_score', 0) or 0)
    event = 'OPEN' if scan_result.get('opened') else 'SKIP'
    reason = scan_result.get('reason', '')

    lessons = []
    if session in ('LUNCH_CHOP', 'EOD_CHOP', 'OPEN_VOLATILE'):
        lessons.append(f'{session}: chop — watching, not forcing entries')
    elif session in ('MORNING_TREND', 'AFTERNOON_MOVE'):
        lessons.append(f'{session}: tradable window — alert for zone+flow')

    if zone.get('active'):
        if zone_dist >= 0 and zone_dist <= 0.6:
            lessons.append(f"In zone ({zone.get('bias')}) — high-quality watch")
        elif zone_dist > 0:
            lessons.append(f"Zone {zone_dist:.1f}% away — waiting for pullback")
    else:
        lessons.append('No evening zone — observing structure only')

    if fs >= 4:
        lessons.append(f'Flow strong ({fs}/6) — institutional alignment possible')
    elif fs < 2:
        lessons.append(f'Flow weak ({fs}/6) — market indecisive')

    if theta.get('level') == 'HIGH':
        lessons.append(f"Theta {theta.get('level')}: {theta.get('note', '')[:60]}")
    if pcr:
        pcr_note = 'bearish crowd' if float(pcr) > 1.1 else 'bullish crowd' if float(pcr) < 0.85 else 'balanced OI'
        lessons.append(f'PCR {pcr} — {pcr_note}')
    if max_pain and price:
        lessons.append(f'Max pain ~{max_pain:,.0f} vs spot {price:,.0f}')

    if chart.get('nearest_resistance') or chart.get('nearest_support'):
        lessons.append(
            f"S/R: sup {chart.get('nearest_support', '—')} / "
            f"res {chart.get('nearest_resistance', '—')}"
        )

    lessons.append(f'15m structure: {struct}')

    try:
        from src.option_greeks import get_greeks_dashboard, format_greeks_lesson
        gd = get_greeks_dashboard()
        ch = gd.get('chain') or {}
        if ch.get('atm_iv_avg'):
            lessons.append(
                f"ATM IV {ch.get('atm_iv_avg', 0)*100:.1f}% "
                f"rank {ch.get('iv_rank', 50):.0f} · PCR {ch.get('pcr', '—')}"
            )
        cg = gd.get('contract') or {}
        gl = format_greeks_lesson(cg)
        if gl:
            lessons.append(f'Greeks: {gl}')
    except Exception:
        pass

    if event == 'SKIP' and reason:
        lessons.append(f'Decision: skip — {reason}')
    elif event == 'OPEN':
        lessons.append('Decision: virtual drill opened — tracking premium path')

    return {
        'event': event,
        'price': price,
        'session': session,
        'bias': scan_result.get('bias') or zone.get('bias', ''),
        'sim_score': sim_score,
        'lesson': ' · '.join(lessons[:6]),
        'metrics': {
            'regime': regime,
            'rsi': rsi,
            'flow_score': fs,
            'vix': vix,
            'pcr': pcr,
            'max_pain': max_pain,
            'theta_level': theta.get('level'),
            'structure_15m': struct,
            'zone_dist_pct': zone_dist,
            'candles_5m': len(c5m),
            'reason': reason,
            'score_reasons': scan_result.get('reasons', [])[:4],
        },
    }


def log_sim_learning(scan_result: dict):
    """Record one learning observation per scan."""
    if not SIM_LEARNING_LOG or not scan_result.get('scanned'):
        return
    try:
        init_learning_log_table()
        snap = build_learning_snapshot(scan_result)
        now = datetime.now(IST)
        conn = _conn()
        conn.execute("""
            INSERT INTO sim_learning_log
            (date, time, event, price, session, bias, sim_score, lesson, metrics_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
            snap['event'], snap['price'], snap['session'], snap.get('bias', ''),
            snap['sim_score'], snap['lesson'][:500],
            json.dumps(snap['metrics'], default=str),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_today_learning_feed(limit: int = 25) -> list:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    try:
        init_learning_log_table()
        conn = _conn()
        rows = conn.execute("""
            SELECT time, event, price, session, bias, sim_score, lesson, metrics_json
            FROM sim_learning_log WHERE date=? ORDER BY id DESC LIMIT ?
        """, (today, limit)).fetchall()
        conn.close()
        out = []
        for t, ev, price, sess, bias, sc, lesson, mj in rows:
            out.append({
                'time': t, 'event': ev, 'price': price, 'session': sess,
                'bias': bias, 'sim_score': sc, 'lesson': lesson,
                'metrics': json.loads(mj) if mj else {},
            })
        return out
    except Exception:
        return []


def learning_day_summary() -> dict:
    """Counts for dashboard."""
    today = datetime.now(IST).strftime('%Y-%m-%d')
    try:
        conn = _conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM sim_learning_log WHERE date=?", (today,)
        ).fetchone()[0]
        opens = conn.execute(
            "SELECT COUNT(*) FROM sim_learning_log WHERE date=? AND event='OPEN'", (today,)
        ).fetchone()[0]
        conn.close()
        return {'observations_today': total, 'virtual_opens': opens,
                'skips_logged': total - opens}
    except Exception:
        return {'observations_today': 0, 'virtual_opens': 0, 'skips_logged': 0}
