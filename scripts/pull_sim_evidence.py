#!/usr/bin/env python3
"""
Pull sim evidence JSONL from server for local audit.

Usage:
  python scripts/pull_sim_evidence.py
  python scripts/pull_sim_evidence.py --summary

Env:
  BOT_SERVER_SSH=root@YOUR_IP
  SIM_EVIDENCE_FILE=sim_evidence.jsonl
"""

import os
import sys
import json
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except Exception:
    pass

LOCAL_DIR = ROOT / 'logs'
LOCAL_FILE = LOCAL_DIR / 'sim_evidence.jsonl'
REMOTE = os.getenv('BOT_SERVER_SSH', 'root@64.227.177.10')
REMOTE_PATH = os.getenv('SIM_EVIDENCE_FILE', 'sim_evidence.jsonl')
REMOTE_FULL = f'{REMOTE}:~/bnf-bot/{REMOTE_PATH}'


def pull() -> bool:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ['scp', '-o', 'StrictHostKeyChecking=accept-new', REMOTE_FULL, str(LOCAL_FILE)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr or r.stdout or 'scp failed')
        return False
    return True


def print_summary():
    if not LOCAL_FILE.exists():
        print('No local evidence — run pull first.')
        return
    today = date.today().isoformat()
    events = Counter()
    scans = 0
    for line in LOCAL_FILE.read_text(encoding='utf-8').splitlines():
        try:
            d = json.loads(line)
            if d.get('date') != today:
                continue
            events[d.get('event', '?')] += 1
            if d.get('event') == 'SIM_SCAN':
                scans += 1
        except json.JSONDecodeError:
            pass
    print(f'Evidence today ({today}):')
    for ev, n in events.most_common():
        print(f'  {ev}: {n}')
    print(f'Total JSONL lines today: {sum(events.values())}')
    if scans == 0:
        print('⚠️ 0 SIM_SCAN events — training not recorded today')


if __name__ == '__main__':
    summary_only = '--summary' in sys.argv
    if not summary_only:
        if pull():
            print(f'Pulled → {LOCAL_FILE}')
        else:
            sys.exit(1)
    print_summary()
