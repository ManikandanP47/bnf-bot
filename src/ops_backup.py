"""
Ops — daily backups and uptime alerts.
"""

import os
import shutil
import json
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
BACKUP_KEEP_DAYS = int(os.getenv('BACKUP_KEEP_DAYS', '7'))
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
ZONE_FILE = 'daily_zone.json'
UPTIME_ALERT_MIN = int(os.getenv('UPTIME_ALERT_MINUTES', '30'))


def run_daily_backup() -> dict:
    """Copy brain DB + zone file; prune backups older than KEEP days."""
    today = datetime.now(IST).strftime('%Y-%m-%d')
    dest = os.path.join(BACKUP_DIR, today)
    os.makedirs(dest, exist_ok=True)
    copied = []

    if os.path.exists(DB_FILE):
        shutil.copy2(DB_FILE, os.path.join(dest, os.path.basename(DB_FILE)))
        copied.append(DB_FILE)
    if os.path.exists(ZONE_FILE):
        shutil.copy2(ZONE_FILE, os.path.join(dest, ZONE_FILE))
        copied.append(ZONE_FILE)

    hb = 'heartbeat.json'
    if os.path.exists(hb):
        shutil.copy2(hb, os.path.join(dest, hb))
        copied.append(hb)

    for extra in ('.groww_token_cache.json', '.groww_rate_limit.json'):
        if os.path.exists(extra):
            shutil.copy2(extra, os.path.join(dest, extra))
            copied.append(extra)

    _prune_old_backups()

    return {'ok': bool(copied), 'dest': dest, 'files': copied}


def _prune_old_backups():
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = datetime.now(IST).date() - timedelta(days=BACKUP_KEEP_DAYS)
    for name in os.listdir(BACKUP_DIR):
        path = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(path):
            continue
        try:
            d = datetime.strptime(name, '%Y-%m-%d').date()
            if d < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except ValueError:
            pass


def format_backup_status() -> str:
    if not os.path.isdir(BACKUP_DIR):
        return 'No backups yet'
    days = sorted(os.listdir(BACKUP_DIR), reverse=True)[:5]
    return f"{len(os.listdir(BACKUP_DIR))} day(s) stored — latest: {days[0] if days else '—'}"


def check_uptime_and_alert(messenger, last_alert_day: int) -> int:
    """
    Alert once per day if heartbeat stale during market hours.
    Returns updated last_alert_day (day of month when alert sent).
    """
    from src.safety import check_trading_day

    now = datetime.now(IST)
    if not check_trading_day().get('trade'):
        return last_alert_day
    if not (9 <= now.hour <= 15 or (now.hour == 15 and now.minute <= 30)):
        return last_alert_day

    try:
        if not os.path.exists('heartbeat.json'):
            return last_alert_day
        with open('heartbeat.json') as f:
            hb = json.load(f)
        last_ts = datetime.fromisoformat(hb.get('timestamp', ''))
        if last_ts.tzinfo is None:
            last_ts = IST.localize(last_ts)
        silence = (now - last_ts).total_seconds() / 60
        if silence >= UPTIME_ALERT_MIN and last_alert_day != now.day:
            messenger.send(
                f"🚨 *Bot uptime alert*\n\n"
                f"No heartbeat for *{int(silence)} min* during market hours.\n"
                f"Last seen: {hb.get('last_seen', '?')}\n\n"
                f"Check server: `systemctl status bnf-bot`\n"
                f"_If a trade is open, check Groww manually._"
            )
            return now.day
    except Exception:
        pass
    return last_alert_day
