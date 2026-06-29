"""
Position persistence — crash recovery across restarts.
Syncs with STATE.position and Groww on startup.
"""

import json
import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
POSITION_FILE = os.getenv('POSITION_STATE_FILE', 'position_state.json')


def save_position(position: dict):
    """Persist open position to disk."""
    try:
        payload = {
            'saved_at': datetime.now(IST).isoformat(),
            'position': position,
        }
        with open(POSITION_FILE, 'w') as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception:
        pass


def load_position() -> dict:
    try:
        if os.path.exists(POSITION_FILE):
            with open(POSITION_FILE) as f:
                data = json.load(f)
            return data.get('position') or {}
    except Exception:
        pass
    return {}


def clear_position():
    try:
        if os.path.exists(POSITION_FILE):
            os.remove(POSITION_FILE)
    except Exception:
        pass
