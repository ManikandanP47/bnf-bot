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
SIM_ONLY_DAYS = int(os.getenv('SIM_ONLY_DAYS', os.getenv('LEARNING_PHASE_DAYS', '14')))
PAPER_PHASE_DAYS = int(os.getenv('PAPER_PHASE_DAYS', '14'))
LEARNING_PHASE_DAYS = SIM_ONLY_DAYS  # backward compat alias
TOTAL_TRAINING_DAYS = SIM_ONLY_DAYS + PAPER_PHASE_DAYS
SHADOW_MAX_PER_DAY = int(os.getenv('SHADOW_MAX_PER_DAY', '5'))
SIM_MAX_OPEN = int(os.getenv('SIM_MAX_OPEN', '2'))
SHADOW_ENABLED = os.getenv('SHADOW_LEARNING', 'true').lower() == 'true'
VIRTUAL_TICK_SEC = int(os.getenv('VIRTUAL_TICK_SEC', '10'))
VIRTUAL_TICK_IDLE_SEC = int(os.getenv('VIRTUAL_TICK_IDLE_SEC', '30'))

_last_virtual_tick = 0.0


def has_open_virtual_orders() -> bool:
    """Any virtual order open today — use faster live monitoring."""
    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchone()[0]
    conn.close()
    return n > 0


def get_open_virtual_positions() -> list:
    """Open virtual orders for feed subscription."""
    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    rows = conn.execute("""
        SELECT strike, opt_type, expiry FROM shadow_trades
        WHERE date=? AND status='OPEN' AND strike > 0 AND expiry != ''
    """, (today,)).fetchall()
    conn.close()
    keys = ['strike', 'opt_type', 'expiry']
    return [dict(zip(keys, r)) for r in rows]


def _conn():
    return sqlite3.connect(DB_FILE)


def _migrate_shadow_columns(conn):
    existing = {r[1] for r in conn.execute("PRAGMA table_info(shadow_trades)").fetchall()}
    for name, typ in [
        ('sim_source', 'TEXT'),
        ('sim_score', 'INTEGER'),
        ('range_note', 'TEXT'),
        ('entry_reasons', 'TEXT'),
        ('mae_prem', 'REAL'),
        ('mfe_prem', 'REAL'),
        ('peak_pnl_rs', 'REAL'),
        ('entry_flow_score', 'INTEGER'),
        ('prem_source', 'TEXT'),
    ]:
        if name not in existing:
            conn.execute(f"ALTER TABLE shadow_trades ADD COLUMN {name} {typ}")


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
    _migrate_shadow_columns(conn)
    conn.commit()
    conn.close()


def _first_activity_date():
    """First day with virtual or confirmed trade activity."""
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
        return None
    try:
        return datetime.strptime(row[0], '%Y-%m-%d').date()
    except ValueError:
        return None


def training_elapsed_days() -> int:
    first = _first_activity_date()
    if not first:
        return 0
    return (datetime.now(IST).date() - first).days


def training_phase() -> str:
    """SIM (week 1–2) → PAPER (week 3–4) → LIVE_READY (month done)."""
    elapsed = training_elapsed_days()
    if elapsed < SIM_ONLY_DAYS:
        return 'SIM'
    if elapsed < TOTAL_TRAINING_DAYS:
        return 'PAPER'
    return 'LIVE_READY'


def is_sim_phase() -> bool:
    return training_phase() == 'SIM'


def is_paper_phase() -> bool:
    return training_phase() == 'PAPER'


def is_learning_phase() -> bool:
    """True for full 4-week training window (blocks live until month ends)."""
    return training_phase() != 'LIVE_READY'


def paper_trading_allowed() -> bool:
    """Paper /execute only in week 3–4 and after."""
    return training_phase() in ('PAPER', 'LIVE_READY')


def is_learning_phase_legacy_sim() -> bool:
    """Alias — virtual sim runs only in SIM phase."""
    return is_sim_phase()


def should_auto_paper_execute() -> bool:
    """
    During learning phase, auto-enter paper trades when all filters pass.
    After graduation, user must tap Execute (precision mode).
    """
    if os.getenv('AUTO_PAPER_LEARNING', 'false').lower() != 'true':
        return False
    if os.getenv('PAPER_MODE', 'true').lower() != 'true':
        return False
    return is_paper_phase()


def format_auto_learning_status() -> str:
    """One-liner for /status and startup — what runs automatically."""
    from src.market_simulator import SIM_MAX_PER_DAY, SIM_MIN_SCORE
    info = learning_phase_info()
    phase = info['phase']
    if phase == 'SIM':
        return (
            f"🎓 *Week 1–2: virtual sim only* ({info['days_left']}d left)\n"
            f"  • Market sim every ~4m — live CE/PE, ₹0 risk (max {SIM_MAX_PER_DAY}/day)\n"
            f"  • Sim min score {SIM_MIN_SCORE} — paper `/execute` locked until week 3\n"
            f"  • Brain + ML learn from every sim close — `/ml` for RF/NN progress"
        )
    if phase == 'PAPER':
        cap = int(os.getenv('LEARNING_MAX_TRADES_DAY', '2'))
        return (
            f"📝 *Week 3–4: paper training* ({info['days_left']}d left)\n"
            f"  • Virtual sim OFF — confirm trades with `/execute` (max {cap}/day)\n"
            f"  • Paper P&L + slippage model — builds `/readiness` stats\n"
            f"  • Live ₹5k after day {TOTAL_TRAINING_DAYS} + all gates green"
        )
    return (
        "🎯 *Month complete* — check `/readiness` before live ₹5k\n"
        "  Paper or live via `/execute` — brain still learns every close"
    )


def learning_phase_info() -> dict:
    init_shadow_tables()
    conn = _conn()
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

    phase = training_phase()
    elapsed = training_elapsed_days()
    if phase == 'SIM':
        days_left = max(0, SIM_ONLY_DAYS - elapsed)
        days_until_paper = days_left
        days_until_live = max(0, TOTAL_TRAINING_DAYS - elapsed)
    elif phase == 'PAPER':
        days_left = max(0, TOTAL_TRAINING_DAYS - elapsed)
        days_until_paper = 0
        days_until_live = days_left
    else:
        days_left = 0
        days_until_paper = 0
        days_until_live = 0

    wr = round(shadow_wins / shadow_total * 100, 1) if shadow_total else 0
    return {
        'phase': phase,
        'in_learning_phase': phase != 'LIVE_READY',
        'days_left': days_left,
        'days_until_paper': days_until_paper,
        'days_until_live': days_until_live,
        'elapsed_days': elapsed,
        'sim_only_days': SIM_ONLY_DAYS,
        'paper_phase_days': PAPER_PHASE_DAYS,
        'total_training_days': TOTAL_TRAINING_DAYS,
        'phase_days': SIM_ONLY_DAYS,
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
        from src.premium_feed import virtual_buy_fill, VIRTUAL_REQUIRE_GROWW
        fill = virtual_buy_fill(strike, opt, expiry)
        if fill.get('ok'):
            prem = fill['premium']
            prem_source = fill['prem_source']
        elif VIRTUAL_REQUIRE_GROWW:
            return {
                'name': name, 'premium': 0, 'prem_source': 'UNAVAILABLE',
            }
        else:
            prem_source = 'DELTA_MODEL'
    else:
        prem_source = 'DELTA_MODEL'

    dyn = get_dynamic_sl_target(prem)
    return {
        'name': name,
        'premium': prem,
        'prem_source': prem_source,
        'lot_cost': prem * 15,
        'sl_prem': dyn.get('sl_prem', round(prem * 0.7)),
        'tgt_prem': dyn.get('tgt_prem', round(prem * 2)),
        'strike': strike,
        'opt_type': opt,
        'expiry': expiry,
        'lots': 1,
    }


def _notify_telegram(msg: str, kind: str = 'open', pnl_rs: float = 0, outcome: str = ''):
    try:
        from src.sim_notify import notify_sim_telegram
        notify_sim_telegram(msg, kind=kind, pnl_rs=pnl_rs, outcome=outcome)
    except Exception:
        try:
            from core.messenger import Messenger
            Messenger().send(msg)
        except Exception:
            pass


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

    from src.market_simulator import SIM_MAX_PER_DAY
    cap = max(SHADOW_MAX_PER_DAY, SIM_MAX_PER_DAY)
    if _shadow_count_today() >= cap:
        return {'opened': False, 'reason': f'max {cap}/day'}

    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    open_n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchone()[0]
    if open_n >= SIM_MAX_OPEN:
        conn.close()
        return {'opened': False, 'reason': f'max {SIM_MAX_OPEN} open'}

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

    flow = STATE.get('market.flow') or {}
    now = datetime.now(IST)
    conn.execute("""
        INSERT INTO shadow_trades (
            date, entry_time, option_name, bias, session, score, regime,
            bnf_entry, strike, opt_type, expiry, entry_prem, sl_prem, tgt_prem,
            prediction, rag_notes, status, sim_source, mae_prem, mfe_prem,
            peak_pnl_rs, entry_flow_score, prem_source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        today, now.strftime('%H:%M'),
        params['name'], bias, signal.get('session', ''),
        signal.get('score', 0), signal.get('regime', ''),
        signal.get('price', 0), params.get('strike', 0),
        params.get('opt_type', 'CE'), params.get('expiry', ''),
        params['premium'], params['sl_prem'], params['tgt_prem'],
        prediction, rag_notes, 'OPEN', 'SETUP',
        params['premium'], params['premium'], 0.0,
        flow.get('flow_score', 0), params.get('prem_source', 'DELTA_MODEL'),
    ))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    try:
        from src.virtual_broker import record_sim_entry
        record_sim_entry(sid, bias, params['premium'], params)
    except Exception:
        pass

    _notify_telegram(
        f"🎓 *Virtual order #{sid}* — Groww LTP fill (no real buy)\n"
        f"{params['name']} @ ₹{params['premium']}\n"
        f"Score {signal.get('score', 0)} | {bias} | {signal.get('session', '')}\n"
        f"📋 {prediction[:100]}\n"
        f"_Groww live LTP + buy/sell friction (spread+slip) — WebSocket repriced until exit_",
        kind='open',
    )

    return {'opened': True, 'id': sid, 'name': params['name'], 'rag': rag_notes}


def tick_shadow_trades():
    """Update open virtual orders — Groww LTP on live tick (default every 10s)."""
    global _last_virtual_tick
    import time as _time

    gap = VIRTUAL_TICK_SEC if has_open_virtual_orders() else VIRTUAL_TICK_IDLE_SEC
    if _time.time() - _last_virtual_tick < max(5, gap - 1):
        return
    _last_virtual_tick = _time.time()

    from core.shared_state import STATE

    init_shadow_tables()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    rows = conn.execute("""
        SELECT id, bnf_entry, entry_prem, sl_prem, tgt_prem, strike, opt_type,
               expiry, bias, option_name, score, session, regime, prediction,
               sim_source, mae_prem, mfe_prem, peak_pnl_rs, entry_flow_score,
               entry_time
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
    flow = STATE.get('market.flow') or {}
    cur_flow = flow.get('flow_score', 0)

    for row in rows:
        (sid, bnf_e, entry_p, sl_p, tgt_p, strike, otype, expiry,
         bias, name, score, session, regime, prediction,
         sim_source, mae_p, mfe_p, peak_pnl, entry_flow, entry_time) = row

        pos = {
            'entry_price': entry_p, 'bnf_at_entry': bnf_e,
            'strike': strike, 'opt_type': otype, 'expiry': expiry,
            'sl_prem': sl_p, 'tgt_prem': tgt_p,
        }
        from src.position_watch import smart_mark_to_market
        mtm = smart_mark_to_market(pos, price)
        est = mtm['premium']
        from src.trade_analytics import virtual_sell_fill_price, virtual_live_pnl, SIM_LIVE_FILLS
        sell = virtual_sell_fill_price(est)
        sell_fill = sell['fill']
        live = virtual_live_pnl(entry_p, est)
        pnl = live['pnl_rs'] if SIM_LIVE_FILLS else mtm['pnl_rs']
        prem_src = mtm.get('prem_source', '')

        if not mtm.get('is_real') and est <= 0:
            continue
        new_mae = min(mae_p or entry_p, est)
        new_mfe = max(mfe_p or entry_p, est)
        new_peak = max(peak_pnl or 0, pnl)

        trail_sl = sl_p
        if est >= entry_p * 1.35:
            trail_sl = max(sl_p, round(entry_p * 1.05, 1))

        exit_now, reason, outcome = False, '', ''

        check_prem = sell_fill if SIM_LIVE_FILLS else est
        if check_prem >= tgt_p:
            exit_now, reason, outcome = True, f'🎯 Target ₹{tgt_p:.0f}', 'WIN'
        elif check_prem <= trail_sl:
            tag = 'trail' if trail_sl > sl_p else 'SL'
            exit_now, reason, outcome = True, f'🛑 {tag} @ ₹{sell_fill:.0f}', 'LOSS'
        elif (entry_flow or 0) >= 3 and cur_flow <= (entry_flow - 2) and pnl < 0:
            exit_now, reason, outcome = True, f'📉 Flow faded ({entry_flow}→{cur_flow})', 'LOSS'
        elif now.time() >= dtime(15, 10):
            exit_now = True
            outcome = 'WIN' if pnl >= 0 else 'LOSS'
            reason = f'⏰ EOD @ ₹{est:.0f}'

        if not exit_now:
            try:
                from src.virtual_broker import record_sim_tick, maybe_record_mid_snapshot
                record_sim_tick(sid, price, est, entry_p, cur_flow, prem_src)
                maybe_record_mid_snapshot(sid, bias or '', price, est, entry_p)
            except Exception:
                pass
            conn.execute("""
                UPDATE shadow_trades SET mae_prem=?, mfe_prem=?, peak_pnl_rs=?
                WHERE id=?
            """, (new_mae, new_mfe, new_peak, sid))
            continue

        lesson = _build_shadow_lesson(
            outcome, bias, session, score, pnl, reason, prediction, price, bnf_e
        )
        conn.execute("""
            UPDATE shadow_trades SET
                exit_time=?, bnf_exit=?, exit_prem=?, pnl_rs=?, outcome=?,
                exit_reason=?, lesson=?, status='CLOSED',
                mae_prem=?, mfe_prem=?, peak_pnl_rs=?
            WHERE id=?
        """, (
            now.strftime('%H:%M'), price, sell_fill if SIM_LIVE_FILLS else est, pnl, outcome, reason, lesson,
            new_mae, new_mfe, new_peak, sid,
        ))

        try:
            from src.virtual_broker import record_sim_exit, format_sim_chart_brief, format_trend_evolution_brief
            from src.position_watch import clear_anchor
            from src.premium_feed import _option_symbol
            record_sim_exit(sid, bias or '', est, outcome, pnl)
            if strike and expiry:
                clear_anchor(_option_symbol(strike, otype, expiry))
            chart_line = format_sim_chart_brief(sid)
            trend_line = format_trend_evolution_brief(sid)
        except Exception:
            chart_line = ''
            trend_line = ''

        src = sim_source or 'SETUP'
        hold_m = 0
        if entry_time:
            try:
                et = datetime.strptime(entry_time, '%H:%M')
                hold_m = max(0, int((now.replace(tzinfo=None) -
                                     et.replace(year=now.year, month=now.month, day=now.day)
                                     ).total_seconds() / 60))
            except ValueError:
                pass
        emoji = '🟢' if outcome == 'WIN' else '🔴'
        close_msg = (
            f"{emoji} *Virtual order #{sid} closed* ({src}) — {outcome}\n"
            f"{name} | Groww LTP P&L: ₹{pnl:,} | peak ₹{new_peak:,}\n"
            f"₹{entry_p:.0f} → ₹{est:.0f} ({prem_src}) | held {hold_m}m\n"
            f"{reason}\n"
        )
        if chart_line:
            close_msg += f"{chart_line}\n"
        if trend_line:
            close_msg += f"{trend_line}\n"
        close_msg += f"🧠 {lesson[:120]}"
        _notify_telegram(close_msg, kind='close', pnl_rs=pnl, outcome=outcome)

        try:
            from agents.learning_agent import BRAIN
            BRAIN.record_shadow_patterns(
                session=session or '', score=score or 0, regime=regime or '',
                rsi=STATE.get('market.rsi_5m', 50), outcome=outcome, pnl_rs=pnl,
                sim_source=src,
            )
        except Exception:
            pass

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
               prediction, rag_notes, status, sim_source, sim_score,
               range_note, peak_pnl_rs, entry_reasons, prem_source
        FROM shadow_trades WHERE date=? ORDER BY id
    """, (today,)).fetchall()
    conn.close()
    keys = ['id', 'entry_time', 'exit_time', 'option_name', 'bias', 'session',
            'score', 'entry_prem', 'exit_prem', 'pnl_rs', 'outcome',
            'exit_reason', 'lesson', 'prediction', 'rag_notes', 'status',
            'sim_source', 'sim_score', 'range_note', 'peak_pnl_rs', 'entry_reasons',
            'prem_source']
    return [dict(zip(keys, r)) for r in rows]


def format_shadow_daily_section() -> str:
    """EOD shadow learning block for Telegram journal."""
    trades = get_today_shadow_trades()
    info = learning_phase_info()
    lines = [
        "",
        "🎓 *Market Simulation* (memory only — no Groww)",
        f"━━━━━━━━━━━━━━━━━━━",
        f"Phase: *{info['phase']}* — {info['days_left']}d left | "
        f"paper in {info['days_until_paper']}d | live in {info['days_until_live']}d",
        f"All-time shadow: {info['shadow_total']} drills | {info['shadow_win_rate']}% win",
    ]
    if not trades:
        lines.append("\n📭 No sim drills today — bot still scanning live flow.")
        return '\n'.join(lines)

    wins = [t for t in trades if t.get('outcome') == 'WIN']
    losses = [t for t in trades if t.get('outcome') == 'LOSS']
    open_t = [t for t in trades if t.get('status') == 'OPEN']
    lines.append(f"\nToday: {len(wins)} win | {len(losses)} loss | {len(open_t)} open")

    for t in trades:
        src = t.get('sim_source') or 'SETUP'
        if t['status'] == 'OPEN':
            lines += [
                "",
                f"⏳ *Sim #{t['id']}* ({src}) {t['option_name']}",
                f"  {t['entry_time']} @ ₹{t['entry_prem']} | score {t.get('sim_score') or t['score']}",
                f"  📍 {t.get('range_note') or '—'}",
            ]
            continue
        e = '🟢' if t['outcome'] == 'WIN' else '🔴'
        lines += [
            "",
            f"{e} *Sim #{t['id']}* ({src}) {t['option_name']}",
            f"  {t['entry_time']}→{t['exit_time']} | ₹{t['entry_prem']}→{t['exit_prem']}",
            f"  P&L: ₹{t['pnl_rs']:,.0f} | peak ₹{t.get('peak_pnl_rs') or 0:,.0f}",
            f"  {t['exit_reason']}",
            f"  🧠 {t.get('lesson', '—')[:100]}",
        ]

    lines.append(
        "\n_Knowledge from shadows is stored and checked on every new setup via RAG_"
    )
    return '\n'.join(lines)


def format_shadow_brief() -> str:
    """Short line for /status or morning brief."""
    info = learning_phase_info()
    if info['phase'] == 'LIVE_READY':
        return "🎯 Month complete — `/readiness` before live ₹5k"
    if info['phase'] == 'PAPER':
        return (
            f"📝 Paper week 3–4: {info['days_left']}d left | "
            f"max {os.getenv('LEARNING_MAX_TRADES_DAY', '2')} trade/day via /execute"
        )
    return (
        f"🎓 Sim week 1–2: {info['days_left']}d left | "
        f"{info['shadow_today']} sims today — paper unlocks in {info['days_until_paper']}d"
    )
