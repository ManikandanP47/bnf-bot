"""
Sim scan journal — logs what the virtual sim saw, scored, skipped, or opened.

Gives full-day visibility: skip reasons, market context, near-misses, lessons.
"""

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
import pytz

from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
SIM_SCAN_LOG = os.getenv('SIM_SCAN_LOG', 'true').lower() == 'true'
SIM_MIN_SCORE = int(os.getenv('SIM_MIN_SCORE', '4'))


def _conn():
    return sqlite3.connect(DB_FILE)


def init_sim_scan_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sim_scan_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT,
            time         TEXT,
            event        TEXT,
            reason       TEXT,
            price        REAL,
            session      TEXT,
            bias         TEXT,
            sim_score    INTEGER,
            flow_score   INTEGER,
            rsi          REAL,
            vwap         REAL,
            in_zone      INTEGER,
            candles_5m   INTEGER,
            market_open  INTEGER,
            score_reasons TEXT,
            opened_id    INTEGER
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_scan_date ON sim_scan_log(date, time)"
    )
    conn.commit()
    conn.close()


def market_snapshot() -> dict:
    """What sim sees right now — attached to every scan log row."""
    zone = STATE.get('zone', {}) or {}
    price = float(STATE.get('market.price', 0) or 0)
    c5m = STATE.get('market.candles_5m', []) or []
    flow = STATE.get('market.flow') or STATE.get('signals.market_flow') or {}
    vwap = float(STATE.get('market.vwap', 0) or 0)
    in_zone = 0
    if zone.get('active') and price > 0:
        low, high = float(zone.get('low', 0) or 0), float(zone.get('high', 0) or 0)
        if low and high and low * 0.995 <= price <= high * 1.005:
            in_zone = 1
    return {
        'price': price,
        'session': STATE.get('market.session', 'CLOSED'),
        'bias': zone.get('bias', '') if zone.get('active') else '',
        'flow_score': int(flow.get('flow_score', 0) or 0),
        'rsi': float(STATE.get('market.rsi_5m', 50) or 50),
        'vwap': vwap,
        'in_zone': in_zone,
        'candles_5m': len(c5m),
        'market_open': 1 if STATE.get('system.market_open') else 0,
        'zone_low': zone.get('low'),
        'zone_high': zone.get('high'),
        'data_source': STATE.get('market.data_source', ''),
    }


def log_sim_scan(result: dict):
    """Persist one sim scan decision (skip, open, cooldown)."""
    if not SIM_SCAN_LOG:
        return
    try:
        init_sim_scan_table()
        snap = result.get('snapshot') or market_snapshot()
        now = datetime.now(IST)
        event = 'SKIP'
        if result.get('opened'):
            event = 'OPEN'
        elif result.get('scanned') is False:
            event = 'COOLDOWN'
        elif not result.get('scanned'):
            return

        reasons = result.get('reasons') or result.get('score_reasons') or []
        if isinstance(reasons, list):
            reasons_txt = '; '.join(reasons[:6])
        else:
            reasons_txt = str(reasons or '')

        conn = _conn()
        conn.execute("""
            INSERT INTO sim_scan_log (
                date, time, event, reason, price, session, bias,
                sim_score, flow_score, rsi, vwap, in_zone, candles_5m,
                market_open, score_reasons, opened_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now.strftime('%Y-%m-%d'),
            now.strftime('%H:%M'),
            event,
            (result.get('reason') or '')[:120],
            snap.get('price', 0),
            snap.get('session', ''),
            result.get('bias') or snap.get('bias', ''),
            int(result.get('sim_score') or 0),
            int(snap.get('flow_score', 0)),
            snap.get('rsi', 0),
            snap.get('vwap', 0),
            snap.get('in_zone', 0),
            snap.get('candles_5m', 0),
            snap.get('market_open', 0),
            reasons_txt,
            int(result.get('id') or 0) or None,
        ))
        conn.commit()
        conn.close()
        _record_scan_evidence(event, result, snap, reasons_txt, now)
    except Exception as e:
        print(f"⚠️ sim_scan_log DB failed: {e}")
        _record_scan_evidence('SKIP', result, result.get('snapshot') or market_snapshot(),
                              str(result.get('reason', '')), datetime.now(IST))


def _record_scan_evidence(event, result, snap, reasons_txt, now):
    try:
        from src.sim_evidence import record_evidence
        record_evidence('SIM_SCAN', {
            'scan_event': event,
            'reason': result.get('reason', ''),
            'sim_score': result.get('sim_score', 0),
            'bias': result.get('bias', ''),
            'opened': bool(result.get('opened')),
            'opened_id': result.get('id'),
            'price': snap.get('price', 0),
            'session': snap.get('session', ''),
            'flow_score': snap.get('flow_score', 0),
            'candles_5m': snap.get('candles_5m', 0),
            'in_zone': snap.get('in_zone', 0),
            'score_reasons': reasons_txt,
            'time': now.strftime('%H:%M'),
        })
    except Exception:
        pass


def get_today_scans() -> list:
    init_sim_scan_table()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    rows = conn.execute("""
        SELECT time, event, reason, price, session, bias, sim_score,
               flow_score, rsi, vwap, in_zone, candles_5m, market_open,
               score_reasons, opened_id
        FROM sim_scan_log WHERE date=? ORDER BY time
    """, (today,)).fetchall()
    conn.close()
    keys = [
        'time', 'event', 'reason', 'price', 'session', 'bias', 'sim_score',
        'flow_score', 'rsi', 'vwap', 'in_zone', 'candles_5m', 'market_open',
        'score_reasons', 'opened_id',
    ]
    return [dict(zip(keys, r)) for r in rows]


def _session_label(session: str) -> str:
    return (session or 'CLOSED').replace('_', ' ')


def format_sim_day_visibility(compact: bool = False) -> str:
    """
    Full-day sim visibility — what it saw, skipped, scored, learned.
    compact=True for midday / on-demand (shorter).
    """
    from src.shadow_learning import (
        get_today_shadow_trades, learning_phase_info, is_sim_phase,
    )
    from src.market_simulator import SIM_MIN_SCORE, SIM_SCAN_MINUTES

    scans = get_today_scans()
    trades = get_today_shadow_trades()
    info = learning_phase_info()
    now = datetime.now(IST)

    lines = [
        f"🔍 *Sim Day Log — {now.strftime('%d %b %Y')}*",
        "━━━━━━━━━━━━━━━━━━━",
        f"Phase: *{info['phase']}* | min score *{SIM_MIN_SCORE}* | scan every *{SIM_SCAN_MINUTES}m*",
    ]

    if not is_sim_phase():
        lines.append("_Virtual sim paused — not in week 1–2 SIM phase._")
        return '\n'.join(lines)

    real_scans = [s for s in scans if s['event'] != 'COOLDOWN']
    opens = [s for s in scans if s['event'] == 'OPEN']
    skips = [s for s in scans if s['event'] == 'SKIP']

    lines += [
        "",
        f"📡 *Scans today:* {len(real_scans)} evaluated | "
        f"{len(opens)} opened | {len(skips)} skipped",
    ]

    if not real_scans:
        lines += [
            "",
            "⚠️ *No scan logs yet*",
            "  Bot may still be warming up, market closed, or sim agent waiting.",
            "  Scans run every ~4m between 9:20–14:45 when market is open.",
        ]
        return '\n'.join(lines)

    # Skip reason breakdown
    reasons = Counter(s['reason'] or 'unknown' for s in skips)
    if reasons:
        lines.append("\n*Why sim skipped (count):*")
        for reason, n in reasons.most_common(8):
            lines.append(f"  • {reason}: *{n}×*")

    # Near misses — highest scores that still failed
    near = sorted(
        [s for s in skips if s.get('sim_score', 0) > 0],
        key=lambda x: x['sim_score'],
        reverse=True,
    )[:3]
    if near:
        lines.append("\n*Closest near-misses:*")
        for s in near:
            z = '✅ in zone' if s.get('in_zone') else '❌ outside zone'
            lines.append(
                f"  {s['time']} score *{s['sim_score']}/{SIM_MIN_SCORE}* | "
                f"BNF {s['price']:,.0f} | {_session_label(s['session'])} | {z}"
            )
            if s.get('score_reasons'):
                lines.append(f"    _{s['score_reasons'][:70]}_")

    # Market context — first, midday-ish, last scan
    lines.append("\n*What sim saw (snapshots):*")
    picks = [real_scans[0]]
    if len(real_scans) > 2:
        picks.append(real_scans[len(real_scans) // 2])
    if len(real_scans) > 1:
        picks.append(real_scans[-1])
    seen = set()
    for s in picks:
        key = s['time']
        if key in seen:
            continue
        seen.add(key)
        z = 'in zone' if s.get('in_zone') else 'outside zone'
        vwap_note = ''
        if s.get('vwap') and s.get('price'):
            side = 'above' if s['price'] > s['vwap'] else 'below'
            vwap_note = f" | VWAP {side}"
        lines.append(
            f"  {s['time']} BNF *{s['price']:,.0f}* | {_session_label(s['session'])} | "
            f"flow {s['flow_score']}/6 | RSI {s['rsi']:.0f}{vwap_note} | {z}"
        )
        if s.get('bias'):
            lines.append(f"    bias: {s['bias']} | 5m candles: {s['candles_5m']}")

    # Session activity
    by_sess = Counter(_session_label(s['session']) for s in real_scans)
    if by_sess:
        lines.append("\n*Scans by session:*")
        for sess, n in by_sess.most_common():
            lines.append(f"  {sess}: {n}")

    # Trades + lessons
    closed = [t for t in trades if t.get('status') == 'CLOSED']
    open_t = [t for t in trades if t.get('status') == 'OPEN']
    if trades:
        lines.append(
            f"\n🎮 *Virtual trades:* {len(closed)} closed | {len(open_t)} open"
        )
        for t in trades[:5 if compact else 10]:
            if t.get('status') == 'OPEN':
                lines.append(f"  ⏳ #{t['id']} {t['option_name']} @ ₹{t['entry_prem']}")
            else:
                e = '🟢' if t.get('outcome') == 'WIN' else '🔴'
                lines.append(
                    f"  {e} #{t['id']} {t['option_name']} ₹{t.get('pnl_rs', 0):,.0f} — "
                    f"{(t.get('lesson') or '—')[:60]}"
                )
    else:
        lines += [
            "",
            "📭 *No virtual trades opened*",
            _explain_no_trades(reasons, real_scans),
        ]

    if not compact:
        lines.append("\n_Type `/simday` anytime | full digest at 3:35 PM_")
    return '\n'.join(lines)


def _explain_no_trades(reasons: Counter, scans: list) -> str:
    """Plain-English summary of why the day was empty."""
    if not scans:
        return "  Sim scanner did not run during market hours."

    top = reasons.most_common(1)
    if not top:
        return "  Filters never aligned — check near-misses above."

    reason, _ = top[0]
    hints = {
        'warming up': (
            "  Main blocker: *candle warmup* — needs 5× five-minute candles (~25m) "
            "after each bot restart. Avoid midday restarts."
        ),
        'no premium': (
            "  Main blocker: *Groww option LTP unavailable* — sim requires live "
            "premium (`VIRTUAL_REQUIRE_GROWW_LTP=true`)."
        ),
        'outside sim window': "  Outside 9:20–14:45 sim window.",
        'paused': "  Bot was paused.",
    }
    for key, hint in hints.items():
        if key in reason.lower():
            return hint

    if reason.startswith('score '):
        return (
            f"  Main blocker: *score too low* ({reason}). "
            f"Needs flow + session + zone + VWAP alignment."
        )
    return f"  Main blocker: *{reason}*"
