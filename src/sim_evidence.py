"""
Sim evidence — mandatory audit trail for virtual training.

Every sim scan, trade open/close, and tick is persisted to:
  • SQLite (sim_scan_log, shadow_trades, sim_ticks, …)
  • sim_evidence.jsonl (append-only, pullable like telegram mirror)

If scans_logged=0 during market hours → training day is INVALID (alert sent).
"""

import json
import os
import sqlite3
from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
EVIDENCE_FILE = os.getenv('SIM_EVIDENCE_FILE', 'sim_evidence.jsonl')
EVIDENCE_JSONL = os.getenv('SIM_EVIDENCE_JSONL', 'true').lower() == 'true'
MAX_JSONL_LINES = int(os.getenv('SIM_EVIDENCE_MAX_LINES', '5000'))


def _conn():
    from src.db_persistence import connect
    return connect()


def _today() -> str:
    return datetime.now(IST).strftime('%Y-%m-%d')


def record_evidence(event: str, payload: dict = None):
    """Append one auditable event — always on during training."""
    payload = dict(payload or {})
    row = {
        'ts': datetime.now(IST).isoformat(),
        'date': _today(),
        'event': event,
        **payload,
    }
    if not EVIDENCE_JSONL:
        return
    try:
        with open(EVIDENCE_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, default=str) + '\n')
        _maybe_trim_jsonl()
    except Exception as e:
        print(f"⚠️ sim evidence write failed: {e}")


def _maybe_trim_jsonl():
    """Trim oldest lines only — never drops today's events. DB is source of truth."""
    try:
        if not os.path.exists(EVIDENCE_FILE):
            return
        with open(EVIDENCE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) <= MAX_JSONL_LINES:
            return
        today = _today()
        today_lines = [ln for ln in lines if f'"date": "{today}"' in ln or f'"date":"{today}"' in ln]
        older = [ln for ln in lines if ln not in today_lines]
        budget = max(MAX_JSONL_LINES - len(today_lines), 0)
        kept = older[-budget:] + today_lines if budget else today_lines
        with open(EVIDENCE_FILE, 'w', encoding='utf-8') as f:
            f.writelines(kept)
    except Exception:
        pass


def get_daily_counts(date: str = None) -> dict:
    """Hard counts from DB + JSONL — source of truth for training validity."""
    date = date or _today()
    counts = {
        'date': date,
        'scans_total': 0,
        'scans_skip': 0,
        'scans_open': 0,
        'shadow_opened': 0,
        'shadow_closed': 0,
        'shadow_open_now': 0,
        'sim_ticks': 0,
        'chart_entries': 0,
        'chart_exits': 0,
        'pattern_memory': 0,
        'patterns_shadow': 0,
        'jsonl_lines_today': 0,
        'funnel_events': 0,
        'ml_labeled_samples': 0,
    }

    try:
        from src.sim_scan_journal import init_sim_scan_table
        init_sim_scan_table()
        conn = _conn()
        counts['scans_total'] = conn.execute(
            "SELECT COUNT(*) FROM sim_scan_log WHERE date=? AND event != 'COOLDOWN'",
            (date,),
        ).fetchone()[0]
        counts['scans_skip'] = conn.execute(
            "SELECT COUNT(*) FROM sim_scan_log WHERE date=? AND event='SKIP'",
            (date,),
        ).fetchone()[0]
        counts['scans_open'] = conn.execute(
            "SELECT COUNT(*) FROM sim_scan_log WHERE date=? AND event='OPEN'",
            (date,),
        ).fetchone()[0]
        counts['shadow_opened'] = conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE date=?", (date,),
        ).fetchone()[0]
        counts['shadow_closed'] = conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='CLOSED'",
            (date,),
        ).fetchone()[0]
        counts['shadow_open_now'] = conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='OPEN'",
            (date,),
        ).fetchone()[0]
        counts['sim_ticks'] = conn.execute("""
            SELECT COUNT(*) FROM sim_ticks st
            JOIN shadow_trades sh ON sh.id = st.shadow_id
            WHERE sh.date=?
        """, (date,)).fetchone()[0]
        counts['chart_entries'] = conn.execute("""
            SELECT COUNT(*) FROM sim_chart_snapshots sc
            JOIN shadow_trades sh ON sh.id = sc.shadow_id
            WHERE sh.date=? AND sc.entry_json IS NOT NULL AND sc.entry_json != ''
        """, (date,)).fetchone()[0]
        counts['chart_exits'] = conn.execute("""
            SELECT COUNT(*) FROM sim_chart_snapshots sc
            JOIN shadow_trades sh ON sh.id = sc.shadow_id
            WHERE sh.date=? AND sc.exit_json IS NOT NULL AND sc.exit_json != ''
        """, (date,)).fetchone()[0]
        try:
            counts['pattern_memory'] = conn.execute(
                "SELECT COUNT(*) FROM pattern_memory"
            ).fetchone()[0]
            counts['patterns_shadow'] = conn.execute(
                "SELECT COUNT(*) FROM pattern_memory WHERE pattern_key LIKE 'shadow:%'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        try:
            counts['funnel_events'] = conn.execute(
                "SELECT COUNT(*) FROM signal_funnel WHERE date=?", (date,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        conn.close()
    except Exception as e:
        counts['db_error'] = str(e)[:80]

    if os.path.exists(EVIDENCE_FILE):
        try:
            with open(EVIDENCE_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get('date') == date:
                            counts['jsonl_lines_today'] += 1
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

    try:
        from src.ml_brain import labeled_sample_count
        counts['ml_labeled_samples'] = labeled_sample_count()
    except Exception:
        try:
            conn = _conn()
            counts['ml_labeled_samples'] = conn.execute(
                "SELECT COUNT(*) FROM shadow_trades WHERE outcome IS NOT NULL"
            ).fetchone()[0]
            conn.close()
        except Exception:
            pass

    return counts


def is_training_day_valid(date: str = None) -> dict:
    """
    A SIM-phase market day is valid only if scans were logged.
    Trades optional — but zero scans = bot was blind, not learning.
    """
    date = date or _today()
    now = datetime.now(IST)
    counts = get_daily_counts(date)

    from src.shadow_learning import is_sim_phase
    in_sim = is_sim_phase()

    # After 9:45 on a weekday, expect scans if sim phase + market was open
    market_should_have_scanned = (
        in_sim
        and now.weekday() < 5
        and now.time() >= dtime(9, 45)
        and date == _today()
    )

    valid = True
    reasons = []

    if in_sim and counts['scans_total'] == 0 and market_should_have_scanned:
        valid = False
        reasons.append('0 scans logged — sim scanner did not record anything')

    if counts['scans_total'] > 0 and counts['jsonl_lines_today'] == 0 and EVIDENCE_JSONL:
        reasons.append('JSONL empty but DB has scans — check sim_evidence.jsonl permissions')

    if counts['shadow_closed'] > 0 and counts['sim_ticks'] == 0:
        reasons.append('trades closed but 0 sim_ticks — monitoring not recorded')

    return {
        'valid': valid,
        'reasons': reasons,
        'counts': counts,
        'in_sim_phase': in_sim,
    }


def format_evidence_report(date: str = None) -> str:
    """Audit dashboard — actual DB/JSONL counts, not narrative."""
    date = date or _today()
    audit = is_training_day_valid(date)
    c = audit['counts']
    now = datetime.now(IST)

    lines = [
        f"📊 *Training Evidence — {date}*",
        "━━━━━━━━━━━━━━━━━━━",
        "_Auditable counts from SQLite + JSONL (not estimates)_",
        "",
        "*Sim scanner (sim_scan_log)*",
        f"  Scans logged: *{c['scans_total']}* (skip {c['scans_skip']} | open {c['scans_open']})",
        "",
        "*Virtual trades (shadow_trades)*",
        f"  Opened today: *{c['shadow_opened']}* | closed: *{c['shadow_closed']}* | open now: {c['shadow_open_now']}",
        "",
        "*Live monitoring (sim_ticks)*",
        f"  Premium updates logged: *{c['sim_ticks']}*",
        "",
        "*Chart snapshots*",
        f"  Entry charts: *{c['chart_entries']}* | exit charts: *{c['chart_exits']}*",
        "",
        "*Brain / ML*",
        f"  pattern_memory: *{c['pattern_memory']}* (shadow: {c['patterns_shadow']})",
        f"  ML labeled samples: *{c['ml_labeled_samples']}*",
        f"  Execute funnel events: *{c['funnel_events']}* (paper path)",
        "",
        f"*JSONL audit file:* `{EVIDENCE_FILE}`",
        f"  Events today: *{c['jsonl_lines_today']}* lines",
    ]

    try:
        from src.db_persistence import format_persistence_line
        lines.append(f"\n💾 *Disk persistence:* {format_persistence_line()}")
        lines.append("_Restart-safe — data in trader_brain.db + sim_evidence.jsonl_")
    except Exception:
        pass

    if audit['valid']:
        lines += ["", "✅ *Training day valid* — scanner recorded market evaluations."]
    else:
        lines += [
            "",
            "❌ *INVALID training day* — do not count toward 14-day plan.",
        ]
        for r in audit['reasons']:
            lines.append(f"  • {r}")
        lines += [
            "",
            "_Fix: no restarts 9:15–3:30, Groww feed up, check /groww /status_",
        ]

    if date == _today():
        lines.append(f"\n_Pulled at {now.strftime('%I:%M %p IST')} | /simday for skip details_")
    return '\n'.join(lines)


def check_evidence_gap_and_alert(messenger, last_alert_day: int = -1) -> int:
    """
    Mid-market alert if SIM phase but zero scans logged.
    Returns last_alert_day if sent.
    """
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return last_alert_day
    if not (dtime(10, 0) <= now.time() <= dtime(14, 0)):
        return last_alert_day

    from src.shadow_learning import is_sim_phase
    if not is_sim_phase():
        return last_alert_day

    c = get_daily_counts()
    if c['scans_total'] > 0:
        return last_alert_day
    if last_alert_day == now.day:
        return last_alert_day

    messenger.send(
        "⚠️ *Evidence gap — sim not recording*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"Time: {now.strftime('%I:%M %p IST')}\n"
        f"Scans logged today: *0*\n\n"
        "Training is *invalid* until scans appear in DB.\n"
        "Check: `/evidence` `/groww` `/status`\n"
        "_Avoid restarts during market hours._"
    )
    record_evidence('EVIDENCE_GAP_ALERT', {'scans': 0})
    return now.day
