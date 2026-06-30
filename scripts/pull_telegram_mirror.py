#!/usr/bin/env python3
"""
Pull Telegram mirror log from server into local logs/ for Cursor agent review.

Usage:
  python scripts/pull_telegram_mirror.py
  python scripts/pull_telegram_mirror.py --limit 30

Env (in .env):
  BOT_SERVER_SSH=root@YOUR_IP
  TELEGRAM_MIRROR_FILE=telegram_mirror.jsonl  (on server)
"""

import os
import sys
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except Exception:
    pass

LOCAL_DIR = ROOT / 'logs'
LOCAL_FILE = LOCAL_DIR / 'telegram_mirror.jsonl'
REMOTE = os.getenv('BOT_SERVER_SSH', 'root@64.227.177.10')
REMOTE_PATH = os.getenv('TELEGRAM_MIRROR_FILE', 'telegram_mirror.jsonl')
REMOTE_FULL = f'{REMOTE}:~/bnf-bot/{REMOTE_PATH}'


def pull() -> bool:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ['scp', '-o', 'StrictHostKeyChecking=accept-new', REMOTE_FULL, str(LOCAL_FILE)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr or r.stdout or 'scp failed')
        return False
    return True


def print_digest(limit: int = 40):
    if not LOCAL_FILE.exists():
        print('No local mirror — run pull first or wait for bot messages on server.')
        return
    lines = LOCAL_FILE.read_text(encoding='utf-8').strip().splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    print(f'=== Telegram mirror ({len(rows)} messages) ===\n')
    for r in rows:
        arrow = 'BOT →' if r.get('dir') == 'out' else 'YOU →'
        print(f"{arrow} {r.get('time', '')}")
        print(r.get('text', ''))
        print('-' * 40)


def main():
    limit = 40
    if '--limit' in sys.argv:
        i = sys.argv.index('--limit')
        limit = int(sys.argv[i + 1])
    if pull():
        print(f'Pulled → {LOCAL_FILE}')
    print_digest(limit)


if __name__ == '__main__':
    main()
