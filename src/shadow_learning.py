"""
Shadow Learning — virtual CE/PE drills with no money.

During the 2-week learning phase the bot:
  • Opens shadow trades in memory when analysis finds a setup
  • Tracks premium / direction until SL, target, or EOD
  • Ingests lessons into RAG so knowledge is REUSED on the next setup
  • Sends end-of-day report: tried / won / lost / learned

Confirmed paper trades (Execute button) stay separate — shadow = bot's gym.
"""

import os
import sqlite3
from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
LEARNING_PHASE_DAYS = int(os.getenv('LEARNING_PHASE_DAYS', '14'))
SHADOW_MAX_PER_DAY = int(os.getenv('SHADOW_MAX_PER_DAY', '5'))
SHADOW_ENABLED = os.getenv('SHADOW_LEARNING', 'true').lower() == 'true'


def _conn():
    return sqlite3.connect(DB_FILE)


def init_shadow_tables():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT,
            entry_time   TEXT,
            exit_time    TEXT,
            option_name  TEXT,
            bias         TEXT,
            session      TEXT,
            score        INTEGER,
            regime       TEXT,
            bnf_entry    REAL,
            bnf_exit     REAL,
            strike       INTEGER,
            opt_type     TEXT,
            expiry       TEXT,
            entry_prem   REAL,
            exit_prem    REAL,
            sl_prem      REAL,
            tgt_prem     REAL,
            pnl_rs       REAL,
            outcome      TEXT,
            exit_reason  TEXT,
            prediction   TEXT,
            lesson       TEXT,
            rag_notes    TEXT,
            status       TEXT DEFAULT 'OPEN'
        )
    """)
    conn.commit()
    conn.close()


def is_learning_phase() -> bool:
    """True during first LEARNING_PHASE_DAYS of bot activity."""
    init_shadow_tables()
    conn = _conn()
    row = conn.execute("""
        SELECT MIN(date) FROM (
            SELECT date FROM shadow_trades
            UNION SELECT date FROM trades WHERE outcome IS NOT NULL
        )
    """).fetchone()
    conn.close()
    if not row or not row[0]:
        return True
    try:
        first = datetime.strptime(row[0], '%Y-%m-%d').date()
        days = (datetime.now(IST).date() - first).days
        return days < LEARNING_PHASE_DAYS
    except ValueError:
        return True


def learning_phase_info() -> dict:
    init_shadow_tables()
    conn = _conn()
    first_row = conn.execute("""
        SELECT MIN(date) FROM (
            SELECT date FROM shadow_trades
            UNION SELECT date FROM trades WHERE outcome IS NOT NULL
        )
    """).fetchone()
    shadow_total = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE outcome IS NOT NULL"
    ).fetchone()[0]
    shadow_wins = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE outcome='WIN'"
    ).fetchone()[0]
    today_shadow = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=?",
        (datetime.now(IST).strftime('%Y-%m-%d'),),
    ).fetchone()[0]
    conn.close()

    in_phase = is_learning_phase()
    days_left = LEARNING_PHASE_DAYS
    if first_row and first_row[0]:
        try:
            first = datetime.strptime(first_row[0], '%Y-%m-%d').date()
            elapsed = (datetime.now(IST).date() - first).days
            days_left = max(0, LEARNING_PHASE_DAYS - elapsed)
        except ValueError:
            pass

    wr = round(shadow_wins / shadow_total * 100, 1) if shadow_total else 0
    return {
        'in_learning_phase': in_phase,
        'days_left': days_left,
        'phase_days': LEARNING_PHASE_DAYS,
        'shadow_total': shadow_total,
        'shadow_wins': shadow_wins,
        'shadow_win_rate': wr,
        'shadow_today': today_shadow,
    }


def _shadow_count_today() -> int:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()
    return n


def _build_shadow_params(signal: dict) -> dict:
    from core.shared_state import STATE
    from src.trade_filters import get_dynamic_sl_target

    zone = STATE.get('zone', {})
    prem = zone.get('premium', 200)
    strike = zone.get('strike', 0)
    opt = zone.get('opt_type', 'CE')
    expiry = zone.get('expiry', '')
    name = zone.get('option_name', '') or f"BNF {strike} {opt}"

    if strike and expiry:
        from src.premium_feed import fetch_option_ltp
        live = fetch_option_ltp(strike, opt, expiry)
        if live > 0:
            prem = live

    dyn = get_dynamic_sl_target(prem)
    return {
        'name': name,
        'premium': prem,
        'lot_cost': prem * 15,
        'sl_prem': dyn.get('sl_prem', round(prem * 0.7)),
        'tgt_prem': dyn.get('tgt_prem', round(prem * 2)),
        'strike': strike,
        'opt_type': opt,
        'expiry': expiry,
        'lots': 1,
    }


def try_open_shadow_trade(signal: dict) -> dict:
    """
    Open a no-money virtual trade when analysis fires.
    Reuses RAG knowledge and logs what was applied.
    """
    if not SHADOW_ENABLED or not signal:
        return {'opened': False, 'reason': 'disabled'}

    from core.shared_state import STATE
    if STATE.get('position.open'):
        return {'opened': False, 'reason': 'real position open'}

    if _shadow_count_today() >= SHADOW_MAX_PER_DAY:
        return {'opened': False, 'reason': f'max {SHADOW_MAX_PER_DAY}/day'}

    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    open_row = conn.execute(
        "SELECT id FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchone()
    if open_row:
        conn.close()
        return {'opened': False, 'reason': 'shadow already open'}

  # Don't duplicate if user already executing same setup
    if STATE.get('signals.awaiting_confirmation') or STATE.get('position.open'):
        conn.close()
        return {'opened': False, 'reason': 'awaiting user trade'}

    params = _build_shadow_params(signal)
    if not params.get('premium'):
        conn.close()
        return {'opened': False, 'reason': 'no params'}

    from src.market_rag import record_rag_usage
    rag_notes = '; '.join(signal.get('rag_notes', [])[:2])
    if not rag_notes:
        from src.market_rag import apply_rag_to_signal
        rag = apply_rag_to_signal(signal)
        rag_notes = '; '.join(rag.get('reasons', [])[:2])
        record_rag_usage(rag.get('lessons', []))

    bias = signal.get('trend', 'BULLISH')
    prediction = (
        f"{bias} — BNF {signal.get('price', 0):,.0f} → "
        f"premium ₹{params['premium']} toward target ₹{params['tgt_prem']}"
    )

    now = datetime.now(IST)
    conn.execute("""
        INSERT INTO shadow_trades (
            date, entry_time, option_name, bias, session, score, regime,
            bnf_entry, strike, opt_type, expiry, entry_prem, sl_prem, tgt_prem,
            prediction, rag_notes, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        today, now.strftime('%H:%M'),
        params['name'], bias, signal.get('session', ''),
        signal.get('score', 0), signal.get('regime', ''),
        signal.get('price', 0), params.get('strike', 0),
        params.get('opt_type', 'CE'), params.get('expiry', ''),
        params['premium'], params['sl_prem'], params['tgt_prem'],
        prediction, rag_notes, 'OPEN',
    ))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return {'opened': True, 'id': sid, 'name': params['name'], 'rag': rag_notes}


def tick_shadow_trades():
    """Update open shadow trades — SL / target / track premium."""
    from core.shared_state import STATE
    from src.premium_feed import get_position_premium, estimate_premium

    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    rows = conn.execute("""
        SELECT id, bnf_entry, entry_prem, sl_prem, tgt_prem, strike, opt_type,
               expiry, bias, option_name, score, session, regime, prediction
        FROM shadow_trades WHERE date=? AND status='OPEN'
    """, (today,)).fetchall()
    if not rows:
        conn.close()
        return

    price = STATE.get('market.price', 0)
    if price <= 0:
        conn.close()
        return

    now = datetime.now(IST)
    for row in rows:
        (sid, bnf_e, entry_p, sl_p, tgt_p, strike, otype, expiry,
         bias, name, score, session, regime, prediction) = row

        pos = {
            'entry_price': entry_p, 'bnf_at_entry': bnf_e,
            'strike': strike, 'opt_type': otype, 'expiry': expiry,
        }
        est = get_position_premium(pos, price)
        if est <= 0:
            est = estimate_premium(entry_p, bnf_e, price, strike, otype)

        exit_now, reason, outcome = False, '', ''
        pnl = round((est - entry_p) * 15, 0)

        if est >= tgt_p:
            exit_now, reason, outcome = True, f'🎯 Shadow target ₹{tgt_p:.0f}', 'WIN'
        elif est <= sl_p:
            exit_now, reason, outcome = True, f'🛑 Shadow SL ₹{sl_p:.0f}', 'LOSS'
        elif now.time() >= dtime(15, 10):
            exit_now = True
            outcome = 'WIN' if pnl >= 0 else 'LOSS'
            reason = f'⏰ Shadow EOD @ ₹{est:.0f}'

        if not exit_now:
            continue

        lesson = _build_shadow_lesson(
            outcome, bias, session, score, pnl, reason, prediction, price, bnf_e
        )
        conn.execute("""
            UPDATE shadow_trades SET
                exit_time=?, bnf_exit=?, exit_prem=?, pnl_rs=?, outcome=?,
                exit_reason=?, lesson=?, status='CLOSED'
            WHERE id=?
        """, (
            now.strftime('%H:%M'), price, est, pnl, outcome, reason, lesson, sid,
        ))

        from src.market_rag import ingest_trade_lesson
        from src.market_context import build_market_context
        ctx = STATE.get('market.context') or {}
        ingest_trade_lesson(
            session=session or '', bias=bias or '', regime=regime or '',
            mistake='SHADOW_' + outcome, lesson=lesson, outcome=outcome,
            cpr_class=(ctx.get('cpr') or {}).get('width_class', ''),
        )

    conn.commit()
    conn.close()


def _build_shadow_lesson(outcome, bias, session, score, pnl, reason,
                         prediction, bnf_exit, bnf_entry) -> str:
    move = bnf_exit - bnf_entry
    dir_ok = (bias == 'BULLISH' and move > 0) or (bias == 'BEARISH' and move < 0)
    if outcome == 'WIN':
        return (
            f"Shadow WIN ₹{pnl:,}: {session} {bias} score {score} — "
            f"prediction held ({reason}). Repeat this combo."
        )
    if dir_ok:
        return (
            f"Shadow LOSS ₹{pnl:,} but BNF direction OK — likely theta/timing. "
            f"{reason}. Tighten exit or avoid late entry."
        )
    return (
        f"Shadow LOSS ₹{pnl:,}: direction wrong vs {bias} call. "
        f"BNF {bnf_entry:,.0f}→{bnf_exit:,.0f}. Review zone + CHoCH."
    )


def resolve_shadow_eod():
    """Force-close any open shadows at journal time."""
    tick_shadow_trades()


def get_today_shadow_trades() -> list:
    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    rows = conn.execute("""
        SELECT id, entry_time, exit_time, option_name, bias, session, score,
               entry_prem, exit_prem, pnl_rs, outcome, exit_reason, lesson,
               prediction, rag_notes, status
        FROM shadow_trades WHERE date=? ORDER BY id
    """, (today,)).fetchall()
    conn.close()
    keys = ['id', 'entry_time', 'exit_time', 'option_name', 'bias', 'session',
            'score', 'entry_prem', 'exit_prem', 'pnl_rs', 'outcome',
            'exit_reason', 'lesson', 'prediction', 'rag_notes', 'status']
    return [dict(zip(keys, r)) for r in rows]


def format_shadow_daily_section() -> str:
    """EOD shadow learning block for Telegram journal."""
    trades = get_today_shadow_trades()
    info = learning_phase_info()
    lines = [
        "",
        "🎓 *Shadow Learning* (virtual — no money)",
        f"━━━━━━━━━━━━━━━━━━━",
        f"Phase: {'🟡 LEARNING' if info['in_learning_phase'] else '🟢 GRADUATED'} "
        f"({info['days_left']}d left of {info['phase_days']})",
        f"All-time shadow: {info['shadow_total']} drills | {info['shadow_win_rate']}% win",
    ]
    if not trades:
        lines.append("\n📭 No shadow drills today — no setup passed analysis.")
        return '\n'.join(lines)

    wins = [t for t in trades if t.get('outcome') == 'WIN']
    losses = [t for t in trades if t.get('outcome') == 'LOSS']
    open_t = [t for t in trades if t.get('status') == 'OPEN']
    lines.append(f"\nToday: {len(wins)} win | {len(losses)} loss | {len(open_t)} open")

    for t in trades:
        if t['status'] == 'OPEN':
            lines += [
                "",
                f"⏳ *Shadow #{t['id']}* {t['option_name']} (open)",
                f"  {t['entry_time']} @ ₹{t['entry_prem']} | score {t['score']}",
                f"  🧠 Used: {t.get('rag_notes') or '—'}",
            ]
            continue
        e = '🟢' if t['outcome'] == 'WIN' else '🔴'
        lines += [
            "",
            f"{e} *Shadow #{t['id']}* {t['option_name']}",
            f"  {t['entry_time']}→{t['exit_time']} | ₹{t['entry_prem']}→{t['exit_prem']}",
            f"  P&L: ₹{t['pnl_rs']:,.0f} | {t['exit_reason']}",
            f"  📋 Predicted: {t.get('prediction', '')[:80]}",
            f"  🧠 Learned: {t.get('lesson', '—')[:100]}",
        ]

    lines.append(
        "\n_Knowledge from shadows is stored and checked on every new setup via RAG_"
    )
    return '\n'.join(lines)


def format_shadow_brief() -> str:
    """Short line for /status or morning brief."""
    info = learning_phase_info()
    if not info['in_learning_phase']:
        return f"🎓 Learning phase complete — precision mode (max 1 trade/day)"
    return (
        f"🎓 Learning phase: {info['days_left']}d left | "
        f"{info['shadow_total']} shadow drills | {info['shadow_win_rate']}% shadow WR"
    )
