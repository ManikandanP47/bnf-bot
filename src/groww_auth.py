"""Groww access token with cache + rate-limit backoff (for tests and scripts)."""

import json
import os
import time
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.groww_token_cache.json')
RATE_LIMIT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.groww_rate_limit.json')
CACHE_TTL_SEC = 3 * 3600  # 3 hours
STALE_CACHE_SEC = 6 * 3600
RATE_LIMIT_COOLDOWN_SEC = 3 * 3600  # 3 hours — Groww often blocks longer than 20 min

_auth_lock = threading.Lock()
_rate_limit_until = 0.0


def _load_rate_limit_until() -> float:
    try:
        if os.path.exists(RATE_LIMIT_FILE):
            with open(RATE_LIMIT_FILE) as f:
                return float(json.load(f).get('until', 0))
    except Exception:
        pass
    return 0.0


def _save_rate_limit_until(until: float):
    global _rate_limit_until
    _rate_limit_until = until
    try:
        with open(RATE_LIMIT_FILE, 'w') as f:
            json.dump({'until': until, 'ts': time.time()}, f)
    except Exception:
        pass


_rate_limit_until = _load_rate_limit_until()


def _load_cache(allow_stale: bool = False) -> Optional[str]:
    try:
        if not os.path.exists(CACHE_FILE):
            return None
        with open(CACHE_FILE) as f:
            data = json.load(f)
        age = time.time() - data.get('ts', 0)
        max_age = STALE_CACHE_SEC if allow_stale else CACHE_TTL_SEC
        if age > max_age:
            return None
        tok = (data.get('token') or '').strip()
        return tok if tok.startswith('eyJ') else None
    except Exception:
        return None


def _save_cache(token: str):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({'token': token, 'ts': time.time()}, f)
    except Exception:
        pass


def _state_token() -> str:
    try:
        from core.shared_state import STATE
        tok = (STATE.get('system.groww_token', '') or '').strip()
        return tok if tok.startswith('eyJ') else ''
    except Exception:
        return ''


def is_rate_limited() -> bool:
    global _rate_limit_until
    if time.time() < _rate_limit_until:
        return True
    _rate_limit_until = _load_rate_limit_until()
    return time.time() < _rate_limit_until


def rate_limit_remaining_sec() -> int:
    if not is_rate_limited():
        return 0
    return max(0, int(_rate_limit_until - time.time()))


def fetch_groww_token(
    force_refresh: bool = False,
    max_retries: int = 1,
    base_delay_sec: int = 120,
) -> str:
    """
    Return a valid Groww JWT with minimal TOTP calls.
    Cooldown persists across restarts via .groww_rate_limit.json.
    """
    global _rate_limit_until

    if not force_refresh:
        for tok in (_state_token(), _load_cache(), os.getenv('GROWW_ACCESS_TOKEN', '').strip()):
            if tok and tok.startswith('eyJ'):
                return tok

    if is_rate_limited():
        stale = _load_cache(allow_stale=True) or _state_token()
        if stale:
            mins = rate_limit_remaining_sec() // 60
            print(f"⏸️ Groww cooldown ({mins}m left) — using cached token")
            return stale
        mins = rate_limit_remaining_sec() // 60
        raise RuntimeError(
            f'Groww rate limited — {mins}m left. Bot uses yfinance until cooldown ends. '
            f'Do not restart bot or run Mac tests during cooldown.'
        )

    with _auth_lock:
        if not force_refresh:
            for tok in (_state_token(), _load_cache(), os.getenv('GROWW_ACCESS_TOKEN', '').strip()):
                if tok and tok.startswith('eyJ'):
                    return tok

        if is_rate_limited():
            stale = _load_cache(allow_stale=True) or _state_token()
            if stale:
                return stale
            raise RuntimeError(f'Groww rate limited — {rate_limit_remaining_sec() // 60}m left')

        import pyotp
        from growwapi import GrowwAPI
        from growwapi.groww.exceptions import GrowwAPIRateLimitException

        secret = os.getenv('GROWW_TOTP_SECRET', '')
        api_key = os.getenv('GROWW_TOTP_TOKEN', '')
        if not secret or not api_key:
            raise ValueError('GROWW_TOTP_SECRET or GROWW_TOTP_TOKEN missing in .env')

        last_err = None
        for attempt in range(max_retries):
            try:
                code = pyotp.TOTP(secret).now()
                token = GrowwAPI.get_access_token(api_key=api_key, totp=code)
                if not token:
                    raise ValueError('get_access_token returned empty')
                _save_cache(token)
                _save_rate_limit_until(0.0)
                try:
                    from core.shared_state import STATE
                    STATE.set('system.groww_token', token)
                except Exception:
                    pass
                print('✅ Groww token refreshed and cached')
                return token
            except GrowwAPIRateLimitException as e:
                last_err = e
                _save_rate_limit_until(time.time() + RATE_LIMIT_COOLDOWN_SEC)
                stale = _load_cache(allow_stale=True) or _state_token()
                if stale:
                    print(
                        f"⏳ Groww 429 — {RATE_LIMIT_COOLDOWN_SEC // 60}h cooldown, "
                        f"using cached token"
                    )
                    return stale
                print(
                    f"⏳ Groww 429 — pausing TOTP for {RATE_LIMIT_COOLDOWN_SEC // 60}h "
                    f"(no cache yet)"
                )
                break
            except Exception as e:
                last_err = e
                break

        stale = _load_cache(allow_stale=True) or _state_token()
        if stale:
            return stale
        raise RuntimeError(f'Groww auth failed: {last_err}')


def seed_rate_limit_cooldown(hours: float = 3.0):
    """Call after a known 429 to stop TOTP attempts across restarts."""
    _save_rate_limit_until(time.time() + int(hours * 3600))
