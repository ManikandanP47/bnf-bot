"""Groww access token with cache + rate-limit backoff (for tests and scripts)."""

import json
import os
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.groww_token_cache.json')
CACHE_TTL_SEC = 3 * 3600  # 3 hours — avoid hammering TOTP endpoint


def _load_cache() -> Optional[str]:
    try:
        if not os.path.exists(CACHE_FILE):
            return None
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get('ts', 0) > CACHE_TTL_SEC:
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


def fetch_groww_token(
    force_refresh: bool = False,
    max_retries: int = 6,
    base_delay_sec: int = 60,
) -> str:
    """
    Return a valid Groww JWT with minimal TOTP calls.
    Order: cache → GROWW_ACCESS_TOKEN env → TOTP (with backoff on 429).
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached
        env_tok = os.getenv('GROWW_ACCESS_TOKEN', '').strip()
        if env_tok.startswith('eyJ'):
            return env_tok

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
            try:
                from core.shared_state import STATE
                STATE.set('system.groww_token', token)
            except Exception:
                pass
            return token
        except GrowwAPIRateLimitException as e:
            last_err = e
            if attempt + 1 >= max_retries:
                break
            wait = base_delay_sec * (attempt + 1)
            print(f"⏳ Groww rate limit — retry in {wait}s ({attempt + 1}/{max_retries})")
            time.sleep(wait)
        except Exception as e:
            last_err = e
            break

    raise RuntimeError(f'Groww auth failed after {max_retries} tries: {last_err}')
