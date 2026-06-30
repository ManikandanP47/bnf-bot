"""
Loss Recovery Protocol — human-like discipline, not revenge trading.

After a loss the bot:
  1. Diagnoses WHY (mistake type from brain)
  2. Runs virtual recovery drills (sim learns afternoon re-entry)
  3. Opens ONE optional recovery slot ONLY if drills + rules pass

Rules (₹5k salary trader):
  - Never same session as the loss
  - Only AFTERNOON_MOVE window (13:00–14:30)
  - Score ≥ RECOVERY_MIN_SCORE (default 9)
  - Recoverable loss types only (direction OK, timing, tight SL)
  - Max 1 recovery/day, max RECOVERY_MAX_PER_WEEK/week
  - Same 1 lot — tighter SL, no size increase
"""

import os
from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')

RECOVERY_ENABLED = os.getenv('RECOVERY_ENABLED', 'true').lower() == 'true'
RECOVERY_MIN_SCORE = int(os.getenv('RECOVERY_MIN_SCORE', '9'))
RECOVERY_MAX_LOSS_RS = float(os.getenv('RECOVERY_MAX_LOSS_RS', '450'))
RECOVERY_MAX_PER_WEEK = int(os.getenv('RECOVERY_MAX_PER_WEEK', '2'))
RECOVERY_SL_PCT = float(os.getenv('RECOVERY_SL_PCT', '0.22'))
RECOVERY_DRILL_MIN_WR = float(os.getenv('RECOVERY_DRILL_MIN_WR', '45'))
RECOVERY_DRILL_MIN_SAMPLES = int(os.getenv('RECOVERY_DRILL_MIN_SAMPLES', '8'))

RECOVERABLE_TYPES = frozenset({
    'SL_TIGHT', 'TIMING', 'MARKET_MOVE', 'THETA_DECAY', 'NONE',
})
BLOCKED_TYPES = frozenset({
    'LOW_SCORE', 'BAD_SESSION', 'RANGING_MARKET',
    'OVERBOUGHT', 'OVERSOLD',
})
RECOVERY_SESSIONS = frozenset({'AFTERNOON_MOVE'})


def _conn():
    from src.db_persistence import connect
    return connect()


def init_recovery_tables():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recovery_drill_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT,
            time            TEXT,
            source          TEXT,
            loss_type       TEXT,
            loss_pnl        REAL,
            loss_session    TEXT,
            recoverable     INTEGER,
            drill_outcome   TEXT DEFAULT 'PENDING',
            afternoon_seen  INTEGER DEFAULT 0,
            lesson          TEXT,
            resolved_at     TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recovery_drill_date ON recovery_drill_log(date)"
    )
    conn.commit()
    conn.close()


def _today() -> str:
    return datetime.now(IST).strftime('%Y-%m-%d')


def _week_key() -> str:
    return datetime.now(IST).strftime('%Y-W%W')


def _recovery_state() -> dict:
    from core.shared_state import STATE
    return STATE.get('recovery') or {}


def _set_recovery(**kwargs):
    from core.shared_state import STATE
    cur = dict(STATE.get('recovery') or {})
    cur.update(kwargs)
    STATE.set('recovery', cur)


def classify_from_exit(data: dict) -> str:
    """Reuse brain-style loss classification from exit context."""
    try:
        from agents.learning_agent import BRAIN
        outcome = 'LOSS'
        reason = data.get('reason', '') or data.get('exit_reason', '')
        return BRAIN._classify(outcome, reason, data)[0]
    except Exception:
        return 'MARKET_MOVE'


def _is_recoverable(loss_type: str, pnl_rs: float) -> bool:
    if loss_type in BLOCKED_TYPES:
        return False
    if loss_type not in RECOVERABLE_TYPES:
        return False
    if abs(pnl_rs) > RECOVERY_MAX_LOSS_RS:
        return False
    return True


def _weekly_recovery_count() -> int:
    init_recovery_tables()
    conn = _conn()
    row = conn.execute("""
        SELECT COUNT(*) FROM recovery_drill_log
        WHERE drill_outcome='USED' AND date >= date('now', '-7 days')
    """).fetchone()
    conn.close()
    return int(row[0] or 0) if row else 0


def log_recovery_drill(source: str, loss_type: str, loss_pnl: float,
                       loss_session: str, recoverable: bool, lesson: str = '') -> int:
    init_recovery_tables()
    now = datetime.now(IST)
    conn = _conn()
    cur = conn.execute("""
        INSERT INTO recovery_drill_log
        (date, time, source, loss_type, loss_pnl, loss_session, recoverable, lesson)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        _today(), now.strftime('%H:%M'), source, loss_type, loss_pnl,
        loss_session, 1 if recoverable else 0, lesson[:240],
    ))
    conn.commit()
    drill_id = cur.lastrowid
    conn.close()
    return drill_id


def activate_recovery_window(loss_data: dict, source: str = 'REAL') -> dict:
    """Called after a losing trade closes — opens trained recovery window."""
    if not RECOVERY_ENABLED:
        return {'active': False, 'reason': 'recovery disabled'}

    pnl = float(loss_data.get('pnl_rs', 0) or 0)
    if pnl >= 0:
        return {'active': False, 'reason': 'not a loss'}

    loss_type = loss_data.get('mistake_type') or classify_from_exit(loss_data)
    session = loss_data.get('session', '') or ''
    recoverable = _is_recoverable(loss_type, pnl)

    drill_id = log_recovery_drill(
        source, loss_type, pnl, session, recoverable,
        lesson=loss_data.get('lesson', '')[:200],
    )

    _set_recovery(
        active=recoverable,
        drill_id=drill_id,
        loss_pnl=pnl,
        loss_type=loss_type,
        loss_session=session,
        loss_time=datetime.now(IST).strftime('%H:%M'),
        used_today=False,
        source=source,
        pending_recovery_trade=False,
    )

    return {
        'active': recoverable,
        'drill_id': drill_id,
        'loss_type': loss_type,
        'recoverable': recoverable,
        'loss_pnl': pnl,
    }


def on_real_loss_closed(loss_data: dict) -> dict:
    """Hook from MonitorAgent after paper/live loss."""
    try:
        from src.pro_loss_prevention import on_loss_rectification, PRO_LOSS_PREVENTION
        if PRO_LOSS_PREVENTION:
            payload = on_loss_rectification(loss_data, source='REAL')
            if payload.get('recovery', {}).get('active'):
                from core.shared_state import STATE
                STATE.set('recovery.sl_pct_override', RECOVERY_SL_PCT)
            return payload
    except Exception:
        pass
    result = activate_recovery_window(loss_data, source='REAL')
    if result.get('active'):
        from core.shared_state import STATE
        STATE.set('recovery.sl_pct_override', RECOVERY_SL_PCT)
    return {'recovery': result}


def on_sim_loss_closed(loss_data: dict) -> dict:
    """Hook from shadow/sim — trains recovery without risking capital."""
    try:
        from src.pro_loss_prevention import on_loss_rectification, PRO_LOSS_PREVENTION
        if PRO_LOSS_PREVENTION:
            return on_loss_rectification(loss_data, source='SIM')
    except Exception:
        pass
    return {'recovery': activate_recovery_window(loss_data, source='SIM')}


def mark_afternoon_setup_seen(score: int):
    """When sim/analysis sees a strong afternoon setup after a drill."""
    if score < RECOVERY_MIN_SCORE:
        return
    sess = ''
    try:
        from src.market_observer import get_current_session
        sess = get_current_session().get('session', '')
    except Exception:
        pass
    if sess not in RECOVERY_SESSIONS:
        return

    conn = _conn()
    conn.execute("""
        UPDATE recovery_drill_log SET afternoon_seen=1
        WHERE date=? AND drill_outcome='PENDING' AND recoverable=1
    """, (_today(),))
    conn.commit()
    conn.close()


def resolve_drill_on_sim_close(sim_outcome: str, pnl_rs: float, session: str, score: int):
    """Resolve pending drills when afternoon virtual trade closes."""
    if session not in RECOVERY_SESSIONS or score < RECOVERY_MIN_SCORE:
        return
    outcome = 'WIN' if sim_outcome == 'WIN' else 'LOSS'
    now = datetime.now(IST).strftime('%H:%M')
    conn = _conn()
    conn.execute("""
        UPDATE recovery_drill_log SET
            drill_outcome=?, resolved_at=?, lesson=lesson || ' | drill ' || ?
        WHERE date=? AND drill_outcome='PENDING' AND recoverable=1
    """, (outcome, now, outcome, _today()))
    conn.commit()
    conn.close()

    try:
        from agents.learning_agent import BRAIN
        r = _recovery_state()
        lt = r.get('loss_type', 'MARKET_MOVE')
        key = f"recovery:{lt}:{session}"
        is_win = 1 if outcome == 'WIN' else 0
        BRAIN._record_observe_key(key, good_avoid=is_win, today=_today())
    except Exception:
        pass


def get_recovery_drill_stats(loss_type: str = '') -> dict:
    init_recovery_tables()
    conn = _conn()
    if loss_type:
        rows = conn.execute("""
            SELECT drill_outcome, COUNT(*) FROM recovery_drill_log
            WHERE drill_outcome IN ('WIN','LOSS') AND loss_type=?
            GROUP BY drill_outcome
        """, (loss_type,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT drill_outcome, COUNT(*) FROM recovery_drill_log
            WHERE drill_outcome IN ('WIN','LOSS')
            GROUP BY drill_outcome
        """).fetchall()
    conn.close()
    wins = sum(n for o, n in rows if o == 'WIN')
    losses = sum(n for o, n in rows if o == 'LOSS')
    total = wins + losses
    wr = round(wins / total * 100, 1) if total else None
    return {'wins': wins, 'losses': losses, 'samples': total, 'wr': wr}


def check_recovery_trade_allowed(signal: dict, trades_today: int,
                                 max_trades: int) -> dict:
    """
    Allow ONE extra trade slot when recovery protocol says it's disciplined.
    Called from RiskAgent when trades_today >= max_trades.
    """
    if not RECOVERY_ENABLED:
        return {'allowed': False, 'reason': 'Recovery protocol disabled'}

    r = _recovery_state()
    if not r.get('active') or r.get('used_today'):
        return {'allowed': False, 'reason': 'No recovery window open'}

    if trades_today < max_trades:
        return {'allowed': False, 'reason': 'Still have normal trade slots'}

    score = int(signal.get('score', 0) or 0)
    session = signal.get('session', '') or ''

    if score < RECOVERY_MIN_SCORE:
        return {
            'allowed': False,
            'reason': (
                f"Recovery needs score ≥{RECOVERY_MIN_SCORE} "
                f"(got {score}) — wait for A+ setup"
            ),
        }

    if session in (r.get('loss_session'), 'OPEN_VOLATILE', 'LUNCH_CHOP', 'EOD_CHOP'):
        return {
            'allowed': False,
            'reason': f"Recovery blocked in {session} — need fresh afternoon session",
        }

    if session not in RECOVERY_SESSIONS:
        return {
            'allowed': False,
            'reason': (
                f"Recovery only in AFTERNOON_MOVE (13:00–14:30), "
                f"not {session or 'now'}"
            ),
        }

    if _weekly_recovery_count() >= RECOVERY_MAX_PER_WEEK:
        return {
            'allowed': False,
            'reason': f"Recovery cap {RECOVERY_MAX_PER_WEEK}/week reached — protect capital",
        }

    stats = get_recovery_drill_stats(r.get('loss_type', ''))
    if stats['samples'] >= RECOVERY_DRILL_MIN_SAMPLES:
        if stats['wr'] is not None and stats['wr'] < RECOVERY_DRILL_MIN_WR:
            return {
                'allowed': False,
                'reason': (
                    f"Virtual drills say recovery rarely works for "
                    f"{r.get('loss_type')} ({stats['wr']}% WR) — skip"
                ),
            }

    from src.capital_guard import check_daily_loss_cap
    cap_blocked = False
    try:
        from src.shadow_learning import training_phase
        if training_phase() == 'SIM':
            from src.sim_wallet import is_account_dead_today
            cap_blocked = is_account_dead_today().get('dead', False)
        else:
            cap_blocked = check_daily_loss_cap().get('blocked', False)
    except Exception:
        cap_blocked = check_daily_loss_cap().get('blocked', False)
    if cap_blocked:
        return {'allowed': False, 'reason': 'Daily loss cap — no recovery'}

    _set_recovery(pending_recovery_trade=True)
    loss_pnl = abs(float(r.get('loss_pnl', 0) or 0))
    return {
        'allowed': True,
        'note': (
            f"🔄 *Recovery trade* (1/day max) — afternoon A+ setup\n"
            f"  Morning loss: ₹{loss_pnl:,.0f} ({r.get('loss_type')})\n"
            f"  Tighter SL {RECOVERY_SL_PCT*100:.0f}% · same 1 lot · no revenge size"
        ),
        'is_recovery': True,
    }


def mark_recovery_used():
    """Call when recovery trade is entered."""
    r = _recovery_state()
    drill_id = r.get('drill_id')
    _set_recovery(used_today=True, active=False, pending_recovery_trade=False)
    if drill_id:
        try:
            conn = _conn()
            conn.execute(
                "UPDATE recovery_drill_log SET drill_outcome='USED', resolved_at=? WHERE id=?",
                (datetime.now(IST).strftime('%H:%M'), drill_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def cancel_pending_recovery():
    """User skipped recovery suggestion — keep window, clear pending flag."""
    r = _recovery_state()
    if r.get('pending_recovery_trade'):
        _set_recovery(pending_recovery_trade=False)


def clear_recovery_eod():
    """End of day — close any open recovery window."""
    _set_recovery(
        active=False, pending_recovery_trade=False,
        drill_id=0, loss_pnl=0, loss_type='',
    )
    from core.shared_state import STATE
    STATE.set('recovery.sl_pct_override', None)


def recovery_status() -> dict:
    """Dashboard / Telegram payload."""
    r = _recovery_state()
    stats = get_recovery_drill_stats()
    lt_stats = get_recovery_drill_stats(r.get('loss_type', '')) if r.get('loss_type') else {}
    pending = 0
    try:
        conn = _conn()
        pending = conn.execute(
            "SELECT COUNT(*) FROM recovery_drill_log WHERE date=? AND drill_outcome='PENDING'",
            (_today(),),
        ).fetchone()[0]
        conn.close()
    except Exception:
        pass

    return {
        'enabled': RECOVERY_ENABLED,
        'active': bool(r.get('active')),
        'used_today': bool(r.get('used_today')),
        'loss_pnl': r.get('loss_pnl'),
        'loss_type': r.get('loss_type'),
        'loss_session': r.get('loss_session'),
        'min_score': RECOVERY_MIN_SCORE,
        'recovery_sessions': list(RECOVERY_SESSIONS),
        'drill_stats': stats,
        'type_drill_stats': lt_stats,
        'pending_drills_today': pending,
        'weekly_used': _weekly_recovery_count(),
        'weekly_cap': RECOVERY_MAX_PER_WEEK,
    }


def format_recovery_telegram_after_loss(ctx: dict) -> str:
    """Human-readable coaching after a loss."""
    if not ctx.get('recoverable'):
        return (
            f"🧠 *Loss diagnosed:* {ctx.get('loss_type', 'UNKNOWN')}\n"
            f"❌ Not recoverable today — walk away, protect ₹5k.\n"
            f"_Virtual sim still drills for future learning._"
        )
    return (
        f"🧠 *Loss Recovery Protocol*\n"
        f"Type: `{ctx.get('loss_type')}` · ₹{abs(ctx.get('loss_pnl', 0)):,.0f}\n\n"
        f"❌ *Not* revenge trading — bot will NOT chase immediately.\n"
        f"✅ *One* recovery chance IF:\n"
        f"  • Afternoon session (13:00–14:30)\n"
        f"  • Score ≥ {RECOVERY_MIN_SCORE} (A+ setup only)\n"
        f"  • Different session than the loss\n"
        f"  • Same 1 lot, tighter SL ({RECOVERY_SL_PCT*100:.0f}%)\n\n"
        f"🎓 Virtual sim is drilling recovery setups now.\n"
        f"Type /recovery for status."
    )
