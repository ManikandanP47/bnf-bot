"""Mirror Telegram in/out to a local JSONL log — for Cursor/agent review without copy-paste."""

import os
import json
import re
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
ENABLED = os.getenv('TELEGRAM_MIRROR_ENABLED', 'true').lower() == 'true'
MIRROR_FILE = os.getenv('TELEGRAM_MIRROR_FILE', 'telegram_mirror.jsonl')
MAX_LINES = int(os.getenv('TELEGRAM_MIRROR_MAX_LINES', '500'))


def _plain(text: str) -> str:
    """Strip Telegram Markdown for readable logs."""
    if not text:
        return ''
    t = re.sub(r'\*([^*]+)\*', r'\1', text)
    t = re.sub(r'_([^_]+)_', r'\1', t)
    t = re.sub(r'`([^`]+)`', r'\1', t)
    return t.strip()


def mirror_message(direction: str, text: str, kind: str = 'text') -> None:
    """
    Append one line to telegram_mirror.jsonl.
    direction: 'out' (bot → you) | 'in' (you → bot)
    """
    if not ENABLED or not text:
        return
    row = {
        'ts': datetime.now(IST).isoformat(),
        'time': datetime.now(IST).strftime('%d %b %Y %I:%M %p IST'),
        'dir': direction,
        'kind': kind,
        'text': _plain(text[:4000]),
    }
    try:
        with open(MIRROR_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        _prune_if_needed()
    except Exception:
        pass


def _prune_if_needed() -> None:
    if MAX_LINES <= 0 or not os.path.exists(MIRROR_FILE):
        return
    try:
        with open(MIRROR_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) <= MAX_LINES:
            return
        with open(MIRROR_FILE, 'w', encoding='utf-8') as f:
            f.writelines(lines[-MAX_LINES:])
    except Exception:
        pass


def format_recent(limit: int = 40) -> str:
    """Human-readable digest of recent mirrored messages."""
    if not os.path.exists(MIRROR_FILE):
        return '_No telegram mirror log yet._'
    try:
        with open(MIRROR_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        return f'Mirror read error: {e}'

    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        return '_Mirror log empty._'

    out = [f'📱 *Telegram mirror* (last {len(rows)} messages)', '']
    for r in rows:
        arrow = '→' if r.get('dir') == 'out' else '←'
        out.append(f"{arrow} *{r.get('time', '?')}*")
        out.append(r.get('text', '')[:800])
        out.append('')
    return '\n'.join(out)
