"""
Structured logging — JSON lines to bot.log for grep and post-mortems.
"""

import json
import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
STRUCTURED_LOG = os.getenv('STRUCTURED_LOG', 'true').lower() == 'true'


def log_event(agent: str, event: str, **fields):
    if not STRUCTURED_LOG:
        return
    row = {
        'ts': datetime.now(IST).isoformat(),
        'agent': agent,
        'event': event,
        **fields,
    }
    try:
        print(json.dumps(row, default=str), flush=True)
    except Exception:
        pass
