"""
Database persistence — survive restarts without losing training data.

• WAL mode + busy timeout on all SQLite connections
• Startup snapshot compares table row counts — alerts if data shrinks
• No code path here deletes learning/sim rows on restart (only scheduled
  prune of sim_ticks older than SIM_TICKS_KEEP_DAYS at 3:40 PM backup)
"""

import json
import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
EVIDENCE_FILE = os.getenv('SIM_EVIDENCE_FILE', 'sim_evidence.jsonl')

# Tables that must only grow (or stay flat) across restarts
TRACKED_TABLES = (
    'shadow_trades',
    'sim_scan_log',
    'sim_ticks',
    'sim_chart_snapshots',
    'sim_mid_snapshots',
    'pattern_memory',
    'trades',
    'knowledge_chunks',
    'signal_funnel',
    'skipped_setups',
    'trade_features',
)

SNAPSHOT_KEY = 'persistence_row_counts'


def connect(db_path: str = None) -> sqlite3.Connection:
    """Open SQLite with crash-safe pragmas (safe across systemd restarts)."""
    path = db_path or DB_FILE
    conn = sqlite3.connect(path, timeout=30.0)
    configure_connection(conn)
    return conn


def configure_connection(conn: sqlite3.Connection):
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA foreign_keys=ON')


def _ensure_flags(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_flags (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)


def get_table_counts() -> dict:
    """Current row counts — source of truth for persistence checks."""
    counts = {'db_file': DB_FILE, 'db_exists': os.path.exists(DB_FILE)}
    if not counts['db_exists']:
        counts['missing'] = True
        return counts

    if os.path.exists(EVIDENCE_FILE):
        try:
            counts['jsonl_bytes'] = os.path.getsize(EVIDENCE_FILE)
        except OSError:
            counts['jsonl_bytes'] = 0

    conn = connect()
    for table in TRACKED_TABLES:
        try:
            counts[table] = conn.execute(
                f'SELECT COUNT(*) FROM {table}'
            ).fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = -1  # table not created yet
    conn.close()
    return counts


def _load_last_snapshot(conn: sqlite3.Connection) -> dict:
    _ensure_flags(conn)
    row = conn.execute(
        'SELECT value FROM bot_flags WHERE key=?', (SNAPSHOT_KEY,)
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return {}


def _save_snapshot(conn: sqlite3.Connection, counts: dict):
    _ensure_flags(conn)
    payload = {
        'saved_at': datetime.now(IST).isoformat(),
        'counts': {k: counts[k] for k in TRACKED_TABLES if k in counts},
        'jsonl_bytes': counts.get('jsonl_bytes', 0),
    }
    conn.execute(
        'INSERT OR REPLACE INTO bot_flags (key, value) VALUES (?, ?)',
        (SNAPSHOT_KEY, json.dumps(payload)),
    )
    conn.commit()


def verify_persistence_on_startup(messenger=None) -> dict:
    """
    After restart: compare DB row counts to last run.
    Alert if any tracked table lost rows (unexpected deletion).
    """
    counts = get_table_counts()
    result = {
        'ok': True,
        'counts': counts,
        'losses': [],
        'first_run': False,
    }

    if counts.get('missing'):
        result['first_run'] = True
        return result

    conn = connect()
    prev = _load_last_snapshot(conn)
    prev_counts = (prev.get('counts') or {}) if prev else {}

    if not prev_counts:
        _save_snapshot(conn, counts)
        conn.close()
        result['first_run'] = True
        return result

    for table in TRACKED_TABLES:
        old = prev_counts.get(table)
        new = counts.get(table, 0)
        if old is None or old < 0:
            continue
        if new >= 0 and new < old:
            result['ok'] = False
            result['losses'].append({
                'table': table,
                'was': old,
                'now': new,
            })

    prev_jsonl = prev.get('jsonl_bytes', 0) or 0
    new_jsonl = counts.get('jsonl_bytes', 0) or 0
    if prev_jsonl > 100 and new_jsonl < prev_jsonl * 0.5:
        result['ok'] = False
        result['losses'].append({
            'table': EVIDENCE_FILE,
            'was': prev_jsonl,
            'now': new_jsonl,
            'note': 'jsonl file shrank — check disk',
        })

    _save_snapshot(conn, counts)
    conn.close()

    if not result['ok'] and messenger:
        lines = [
            '⚠️ *Data loss detected after restart*',
            '━━━━━━━━━━━━━━━━━━━',
            '_Row counts dropped — training data may be damaged._',
            '',
        ]
        for loss in result['losses']:
            t = loss['table']
            lines.append(f"  • {t}: {loss['was']} → {loss['now']}")
        lines += [
            '',
            'Check backups: `backups/YYYY-MM-DD/`',
            'Do not delete trader_brain.db manually.',
        ]
        try:
            messenger.send('\n'.join(lines))
        except Exception:
            pass

    try:
        from src.sim_evidence import record_evidence
        record_evidence('STARTUP_PERSISTENCE', {
            'ok': result['ok'],
            'first_run': result['first_run'],
            'losses': result['losses'],
            'shadow_trades': counts.get('shadow_trades', 0),
            'sim_scan_log': counts.get('sim_scan_log', 0),
            'pattern_memory': counts.get('pattern_memory', 0),
        })
    except Exception:
        pass

    return result


def format_persistence_line() -> str:
    """One-line summary for /status or /evidence."""
    c = get_table_counts()
    if c.get('missing'):
        return 'DB: not created yet'
    return (
        f"DB persisted: shadow *{c.get('shadow_trades', 0)}* | "
        f"scans *{c.get('sim_scan_log', 0)}* | "
        f"patterns *{c.get('pattern_memory', 0)}* | "
        f"ticks *{c.get('sim_ticks', 0)}*"
    )
