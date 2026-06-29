"""Groww access token with cache + rate-limit backoff (for tests and scripts)."""

import json
import os
import time
import threading
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.groww_token_cache.json')
CACHE_TTL_SEC = 3 * 3600  # 3 hours — avoid hammering TOTP endpoint
STALE_CACHE_SEC = 6 * 3600  # use stale token when rate-limited (up to 6h)
RATE_LIMIT_COOLDOWN_SEC = 20 * 60  # after 429, stop TOTP calls for 20 min

_auth_lock = threading.Lock()
_rate_limit_until = 0.0


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
    return time.time() < _rate_limit_until


def rate_limit_remaining_sec() -> int:
    return max(0, int(_rate_limit_until - time.time()))


def fetch_groww_token(
    force_refresh: bool = False,
    max_retries: int = 4,
    base_delay_sec: int = 60,
) -> str:
    """
    Return a valid Groww JWT with minimal TOTP calls.
    Order: in-memory STATE → file cache → GROWW_ACCESS_TOKEN → TOTP (locked, backoff on 429).
    """
    global _rate_limit_until

    if not force_refresh:
        for tok in (_state_token(), _load_cache(), os.getenv('GROWW_ACCESS_TOKEN', '').strip()):
            if tok and tok.startswith('eyJ'):
                return tok

    if is_rate_limited():
        stale = _load_cache(allow_stale=True) or _state_token()
        if stale:
            print(
                f"⏸️ Groww rate limit cooldown ({rate_limit_remaining_sec()}s left) "
                f"— using cached token"
            )
            return stale
        raise RuntimeError(
            f'Groww rate limited — retry in {rate_limit_remaining_sec()}s. '
            f'Do not run tests on Mac while server bot is live.'
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
            raise RuntimeError(f'Groww rate limited — retry in {rate_limit_remaining_sec()}s')

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
                _rate_limit_until = 0.0
                try:
                    from core.shared_state import STATE
                    STATE.set('system.groww_token', token)
                except Exception:
                    pass
                return token
            except GrowwAPIRateLimitException as e:
                last_err = e
                _rate_limit_until = time.time() + RATE_LIMIT_COOLDOWN_SEC
                stale = _load_cache(allow_stale=True) or _state_token()
                if stale:
                    print(
                        f"⏳ Groww 429 — cooldown {RATE_LIMIT_COOLDOWN_SEC // 60}m, "
                        f"using cached token"
                    )
                    return stale
                if attempt + 1 >= max_retries:
                    break
                wait = base_delay_sec * (attempt + 1)
                print(f"⏳ Groww rate limit — retry in {wait}s ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            except Exception as e:
                last_err = e
                break

        stale = _load_cache(allow_stale=True) or _state_token()
        if stale:
            return stale
        raise RuntimeError(f'Groww auth failed after {max_retries} tries: {last_err}')
