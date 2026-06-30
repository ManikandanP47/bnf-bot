"""
Virtual Broker — Groww API + live chart training (no real orders).

Training loop:
  1. virtual_buy_fill()  → Groww option LTP = virtual entry price
  2. Every 10s (live)    → Groww LTP re-price when BNF updates or monitor tick
  3. Every 5m (open)     → chart/candle/trend snapshot (sim_mid_snapshots)
  4. On exit             → exit snapshot + trend evolution + pattern_memory + RAG
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
MID_SNAPSHOT_MIN = int(os.getenv('SIM_MID_SNAPSHOT_MIN', '5'))

_last_mid_snap: dict = {}  # shadow_id -> epoch


def _conn():
    from src.db_persistence import connect
    return connect()


def init_virtual_broker_tables():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sim_ticks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_id   INTEGER,
            tick_time   TEXT,
            bnf_price   REAL,
            premium     REAL,
            pnl_rs      REAL,
            flow_score  INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_sim_ticks_shadow ON sim_ticks(shadow_id);

        CREATE TABLE IF NOT EXISTS sim_chart_snapshots (
            shadow_id   INTEGER PRIMARY KEY,
            entry_json  TEXT,
            exit_json   TEXT,
            patterns_json TEXT,
            market_treatment TEXT,
            trend_evolution TEXT
        );

        CREATE TABLE IF NOT EXISTS sim_mid_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_id   INTEGER,
            snap_time   TEXT,
            bnf_price   REAL,
            premium     REAL,
            pnl_rs      REAL,
            chart_json  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sim_mid_shadow ON sim_mid_snapshots(shadow_id);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sim_chart_snapshots)").fetchall()}
    if 'trend_evolution' not in cols:
        conn.execute("ALTER TABLE sim_chart_snapshots ADD COLUMN trend_evolution TEXT")
    conn.commit()
    conn.close()


def _candle_summary(candles: list, n: int = 5) -> list:
    """Compact last N candles for storage."""
    out = []
    for c in (candles or [])[-n:]:
        out.append({
            't': c.get('time', ''),
            'o': round(c.get('open', 0), 1),
            'h': round(c.get('high', 0), 1),
            'l': round(c.get('low', 0), 1),
            'c': round(c.get('close', 0), 1),
            'v': c.get('volume', 0),
        })
    return out


def _build_chart_context(bias: str) -> dict:
    """Full chart + candle context at a point in time."""
    from core.shared_state import STATE
    from agents.analysis_agent import get_structure, check_choch
    from src.chart_levels import compute_chart_levels
    from src.trend_strength import calc_adx

    price = STATE.get('market.price', 0)
    c1m = STATE.get('market.candles_1m', [])
    c5m = STATE.get('market.candles_5m', [])
    c15m = STATE.get('market.candles_15m', [])
    flow = STATE.get('market.flow') or {}

    struct_15 = get_structure(c15m)
    choch_5 = check_choch(c5m, bias)
    chart = compute_chart_levels(c5m, c15m, price)
    adx = calc_adx(c15m)

    return {
        'bnf': price,
        'vwap': STATE.get('market.vwap', 0),
        'rsi_5m': STATE.get('market.rsi_5m', 50),
        'rsi_1m': STATE.get('market.rsi_1m', 50),
        'session': STATE.get('market.session', ''),
        'regime': STATE.get('market.regime', ''),
        'flow_score': flow.get('flow_score', 0),
        'structure_15m': struct_15.get('trend'),
        'choch_5m': choch_5.get('confirmed', False),
        'adx': adx,
        'chart': {
            'support': chart.get('support'),
            'resistance': chart.get('resistance'),
            'pdh': chart.get('pdh'),
            'pdl': chart.get('pdl'),
        },
        'candles_1m': _candle_summary(c1m, 5),
        'candles_5m': _candle_summary(c5m, 8),
        'candles_15m': _candle_summary(c15m, 5),
    }


def _detect_patterns(c5m: list) -> list:
    """Simple candle patterns on 5m for storage."""
    patterns = []
    if len(c5m) < 2:
        return patterns
    a, b = c5m[-2], c5m[-1]
    body_a = abs(a['close'] - a['open'])
    body_b = abs(b['close'] - b['open'])
    if body_b > body_a * 1.5 and b['close'] > b['open']:
        patterns.append('strong_bull_candle')
    if body_b > body_a * 1.5 and b['close'] < b['open']:
        patterns.append('strong_bear_candle')
    if b['low'] > a['low'] and b['close'] > a['close']:
        patterns.append('higher_low')
    if b['high'] < a['high'] and b['close'] < a['close']:
        patterns.append('lower_high')
    return patterns


def maybe_record_mid_snapshot(shadow_id: int, bias: str, bnf: float,
                              premium: float, entry_prem: float):
    """Every N minutes: snapshot live chart/trend while virtual order is open."""
    import time
    now_ts = time.time()
    last = _last_mid_snap.get(shadow_id, 0)
    if now_ts - last < MID_SNAPSHOT_MIN * 60:
        return

    init_virtual_broker_tables()
    ctx = _build_chart_context(bias)
    pnl = round((premium - entry_prem) * 15, 0)
    snap_time = datetime.now(IST).strftime('%H:%M:%S')

    conn = _conn()
    conn.execute("""
        INSERT INTO sim_mid_snapshots
        (shadow_id, snap_time, bnf_price, premium, pnl_rs, chart_json)
        VALUES (?,?,?,?,?,?)
    """, (shadow_id, snap_time, bnf, premium, pnl, json.dumps(ctx)))
    conn.commit()
    conn.close()
    _last_mid_snap[shadow_id] = now_ts


def _analyze_trend_evolution(entry_json: str, exit_json: str,
                             shadow_id: int) -> str:
    """Compare entry → mid → exit structure for learning."""
    try:
        entry = json.loads(entry_json or '{}')
        exit_s = json.loads(exit_json or '{}')
    except json.JSONDecodeError:
        return ''

    e = entry.get('chart') or {}
    x = exit_s.get('chart') or {}

    conn = _conn()
    mids = conn.execute("""
        SELECT snap_time, bnf_price, pnl_rs, chart_json FROM sim_mid_snapshots
        WHERE shadow_id=? ORDER BY id
    """, (shadow_id,)).fetchall()
    conn.close()

    parts = [
        f"entry 15m={e.get('structure_15m')} ADX={e.get('adx', 0):.0f} "
        f"flow={e.get('flow_score')}",
    ]
    for i, (t, bnf, pnl, cj) in enumerate(mids, 1):
        try:
            ch = json.loads(cj or '{}')
            parts.append(
                f"mid{i}@{t}: BNF {bnf:,.0f} P&L ₹{pnl:,} "
                f"15m={ch.get('structure_15m')} RSI={ch.get('rsi_5m', 0):.0f}"
            )
        except json.JSONDecodeError:
            pass

    parts.append(
        f"exit 15m={x.get('structure_15m')} ADX={x.get('adx', 0):.0f} "
        f"flow={x.get('flow_score')}"
    )
    struct_e, struct_x = e.get('structure_15m'), x.get('structure_15m')
    if struct_e == struct_x:
        parts.append('trend: structure held through trade')
    elif struct_e == 'BULLISH' and struct_x == 'BEARISH':
        parts.append('trend: reversed bearish — CHoCH failure risk')
    elif struct_e == 'BEARISH' and struct_x == 'BULLISH':
        parts.append('trend: reversed bullish — PE risk')
    else:
        parts.append(f'trend: {struct_e}→{struct_x}')
    return ' | '.join(parts)


def record_sim_entry(shadow_id: int, bias: str, entry_prem: float,
                     params: dict = None):
    """
    Virtual BUY fill — snapshot market/chart at exact entry moment.
    """
    init_virtual_broker_tables()
    from core.shared_state import STATE

    ctx = _build_chart_context(bias)
    c5m = STATE.get('market.candles_5m', [])
    patterns = _detect_patterns(c5m)

    entry = {
        'time': datetime.now(IST).strftime('%H:%M:%S'),
        'premium': entry_prem,
        'prem_source': (params or {}).get('prem_source', ''),
        'strike': (params or {}).get('strike'),
        'opt_type': (params or {}).get('opt_type'),
        'chart': ctx,
    }

    conn = _conn()
    conn.execute("""
        INSERT OR REPLACE INTO sim_chart_snapshots
        (shadow_id, entry_json, patterns_json)
        VALUES (?,?,?)
    """, (shadow_id, json.dumps(entry), json.dumps(patterns)))
    conn.commit()
    conn.close()
    try:
        from src.sim_evidence import record_evidence
        record_evidence('TRADE_OPEN', {
            'shadow_id': shadow_id,
            'bias': bias,
            'premium': entry_prem,
            'strike': (params or {}).get('strike'),
            'opt_type': (params or {}).get('opt_type'),
            'prem_source': (params or {}).get('prem_source', ''),
        })
    except Exception:
        pass
                    entry_prem: float, flow_score: int = 0,
                    prem_source: str = ''):
    """Log one monitoring tick — Groww LTP re-price."""
    init_virtual_broker_tables()
    pnl = round((premium - entry_prem) * 15, 0)
    now = datetime.now(IST).strftime('%H:%M:%S')
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sim_ticks)").fetchall()}
    if 'prem_source' not in cols:
        conn.execute("ALTER TABLE sim_ticks ADD COLUMN prem_source TEXT")
    conn.execute("""
        INSERT INTO sim_ticks
        (shadow_id, tick_time, bnf_price, premium, pnl_rs, flow_score, prem_source)
        VALUES (?,?,?,?,?,?,?)
    """, (shadow_id, now, bnf, premium, pnl, flow_score, prem_source))
    conn.commit()
    conn.close()


def record_sim_exit(shadow_id: int, bias: str, exit_prem: float,
                    outcome: str, pnl_rs: float):
    """
    Virtual SELL fill — snapshot chart at exit + how market treated setup.
    """
    init_virtual_broker_tables()
    ctx = _build_chart_context(bias)
    exit_snap = {
        'time': datetime.now(IST).strftime('%H:%M:%S'),
        'premium': exit_prem,
        'outcome': outcome,
        'pnl_rs': pnl_rs,
        'chart': ctx,
    }

    conn = _conn()
    row = conn.execute(
        "SELECT entry_json, patterns_json FROM sim_chart_snapshots WHERE shadow_id=?",
        (shadow_id,),
    ).fetchone()

    treatment = _analyze_market_treatment(
        row[0] if row else '{}', exit_snap, row[1] if row else '[]', bias
    )
    trend_evo = _analyze_trend_evolution(
        row[0] if row else '{}', json.dumps(exit_snap), shadow_id
    )

    conn.execute("""
        UPDATE sim_chart_snapshots SET
            exit_json=?, market_treatment=?, trend_evolution=?
        WHERE shadow_id=?
    """, (json.dumps(exit_snap), treatment, trend_evo, shadow_id))
    conn.commit()
    conn.close()

    _update_chart_pattern_memory(shadow_id, treatment, outcome, pnl_rs)
    _ingest_trend_lesson(bias, outcome, treatment, trend_evo, pnl_rs)
    _last_mid_snap.pop(shadow_id, None)
    try:
        from src.sim_evidence import record_evidence
        record_evidence('TRADE_CLOSE', {
            'shadow_id': shadow_id,
            'outcome': outcome,
            'pnl_rs': pnl_rs,
            'exit_prem': exit_prem,
            'treatment': treatment[:120] if treatment else '',
        })
    except Exception:
        pass
    try:
        from src.ml_brain import maybe_retrain
        maybe_retrain()
    except Exception:
        pass


def _ingest_trend_lesson(bias: str, outcome: str, treatment: str,
                         trend_evo: str, pnl_rs: float):
    """Push chart/trend learning into RAG for future setups."""
    if not trend_evo:
        return
    lesson = f"{trend_evo}. {treatment}"
    try:
        from src.market_rag import ingest_trade_lesson
        from core.shared_state import STATE
        ctx = STATE.get('market.context') or {}
        ingest_trade_lesson(
            session=STATE.get('market.session', ''),
            bias=bias, regime=STATE.get('market.regime', ''),
            mistake='SIM_' + outcome, lesson=lesson[:400], outcome=outcome,
            cpr_class=(ctx.get('cpr') or {}).get('width_class', ''),
        )
    except Exception:
        pass


def _analyze_market_treatment(entry_json: str, exit_snap: dict,
                            patterns_json: str, bias: str) -> str:
    """Human-readable: how market treated this virtual order."""
    try:
        entry = json.loads(entry_json)
        patterns = json.loads(patterns_json)
    except json.JSONDecodeError:
        entry, patterns = {}, []

    e_chart = (entry.get('chart') or {})
    x_chart = (exit_snap.get('chart') or {})
    bnf_e = e_chart.get('bnf', 0)
    bnf_x = x_chart.get('bnf', 0)
    move = bnf_x - bnf_e if bnf_e and bnf_x else 0

    dir_ok = (bias == 'BULLISH' and move > 0) or (bias == 'BEARISH' and move < 0)
    parts = [
        f"BNF {bnf_e:,.0f}→{bnf_x:,.0f} ({move:+,.0f})",
        f"structure {e_chart.get('structure_15m')}→{x_chart.get('structure_15m')}",
        f"flow {e_chart.get('flow_score')}→{x_chart.get('flow_score')}",
    ]
    if patterns:
        parts.append(f"patterns@{entry.get('time', '')}: {','.join(patterns)}")
    if dir_ok and exit_snap.get('outcome') == 'LOSS':
        parts.append('market: direction OK but premium lost (theta/timing)')
    elif not dir_ok and exit_snap.get('outcome') == 'LOSS':
        parts.append('market: moved against bias')
    elif exit_snap.get('outcome') == 'WIN':
        parts.append('market: setup worked as predicted')
    return ' | '.join(parts)


def _update_chart_pattern_memory(shadow_id: int, treatment: str,
                                 outcome: str, pnl_rs: float):
    """Store chart-level pattern stats for future lookups."""
    conn = _conn()
    row = conn.execute(
        "SELECT patterns_json, entry_json FROM sim_chart_snapshots WHERE shadow_id=?",
        (shadow_id,),
    ).fetchone()
    conn.close()
    if not row:
        return

    try:
        patterns = json.loads(row[0] or '[]')
        entry = json.loads(row[1] or '{}')
    except json.JSONDecodeError:
        return

    session = (entry.get('chart') or {}).get('session', '')
    struct = (entry.get('chart') or {}).get('structure_15m', '')
    is_win = 1 if outcome == 'WIN' else 0
    today = datetime.now(IST).strftime('%Y-%m-%d')

    keys = [f"chart:{p}" for p in patterns]
    keys += [
        f"chart:struct:{struct}",
        f"chart:session:{session}",
        f"chart:struct:{struct}|session:{session}",
    ]

    conn = _conn()
    for k in keys:
        conn.execute("""
            INSERT INTO pattern_memory
            (pattern_key, wins, losses, total_pnl, samples, last_seen)
            VALUES (?,?,?,?,1,?)
            ON CONFLICT(pattern_key) DO UPDATE SET
                wins=wins+?, losses=losses+?, total_pnl=total_pnl+?,
                samples=samples+1, last_seen=?
        """, (k, is_win, 1 - is_win, pnl_rs, today,
              is_win, 1 - is_win, pnl_rs, today))
    conn.commit()
    conn.close()


def get_sim_tick_count(shadow_id: int) -> int:
    init_virtual_broker_tables()
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM sim_ticks WHERE shadow_id=?", (shadow_id,)
    ).fetchone()[0]
    conn.close()
    return n


def format_sim_chart_brief(shadow_id: int) -> str:
    """One-liner chart context for Telegram."""
    init_virtual_broker_tables()
    conn = _conn()
    row = conn.execute(
        "SELECT entry_json, exit_json, market_treatment, patterns_json "
        "FROM sim_chart_snapshots WHERE shadow_id=?",
        (shadow_id,),
    ).fetchone()
    ticks = conn.execute(
        "SELECT COUNT(*) FROM sim_ticks WHERE shadow_id=?", (shadow_id,)
    ).fetchone()[0]
    conn.close()
    if not row:
        return ''
    try:
        entry = json.loads(row[0] or '{}')
        patterns = json.loads(row[3] or '[]')
    except json.JSONDecodeError:
        return ''
    ch = entry.get('chart') or {}
    pat = ','.join(patterns) if patterns else '—'
    return (
        f"📈 Chart@{entry.get('time', '')}: "
        f"15m {ch.get('structure_15m')} | ADX {ch.get('adx', 0):.0f} | "
        f"patterns [{pat}] | {ticks} Groww ticks"
    )


def format_trend_evolution_brief(shadow_id: int) -> str:
    """Trend path entry → mid → exit."""
    init_virtual_broker_tables()
    conn = _conn()
    row = conn.execute(
        "SELECT trend_evolution FROM sim_chart_snapshots WHERE shadow_id=?",
        (shadow_id,),
    ).fetchone()
    mid_n = conn.execute(
        "SELECT COUNT(*) FROM sim_mid_snapshots WHERE shadow_id=?", (shadow_id,)
    ).fetchone()[0]
    conn.close()
    if not row or not row[0]:
        return f"📊 {mid_n} mid-chart snapshots"
    return f"📊 Trend ({mid_n} mid snaps): {row[0][:150]}"


def prune_old_sim_ticks(keep_days: int = 30) -> int:
    """Delete sim_ticks older than keep_days (by shadow trade date)."""
    cutoff = (datetime.now(IST) - timedelta(days=keep_days)).strftime('%Y-%m-%d')
    conn = _conn()
    try:
        cur = conn.execute("""
            DELETE FROM sim_ticks
            WHERE shadow_id IN (
                SELECT id FROM shadow_trades WHERE date < ?
            )
        """, (cutoff,))
        conn.commit()
        return cur.rowcount
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
