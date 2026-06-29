"""
Global Groww REST rate-limit guard.

Catches 429 / rate-limit errors on any Groww API call (LTP, orders, etc.)
and applies the same cooldown as TOTP auth (via seed_rate_limit_cooldown).
"""

import functools


def is_rest_rate_limit_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    if '429' in s or 'rate limit' in s or 'too many request' in s:
        return True
    try:
        from growwapi.groww.exceptions import GrowwAPIRateLimitException
        return isinstance(exc, GrowwAPIRateLimitException)
    except ImportError:
        return False


def mark_rest_rate_limited(source: str = 'REST'):
    """Persist cooldown so all Groww calls back off."""
    try:
        from src.groww_auth import seed_rate_limit_cooldown, rate_limit_remaining_sec
        seed_rate_limit_cooldown(hours=3.0)
        mins = rate_limit_remaining_sec() // 60
        try:
            from core.shared_state import STATE
            STATE.add_error(f'Groww {source} 429 — cooldown {mins}m')
            STATE.set('system.groww_rate_limit', f'REST 429 — {mins}m left')
        except Exception:
            pass
    except Exception:
        pass


def groww_call(label: str, fn, *args, **kwargs):
    """Run a Groww SDK call with rate-limit detection."""
    from src.groww_auth import is_rate_limited, rate_limit_remaining_sec

    if is_rate_limited():
        mins = rate_limit_remaining_sec() // 60
        raise RuntimeError(f'Groww API paused — {mins}m cooldown ({label})')

    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if is_rest_rate_limit_error(e):
            mark_rest_rate_limited(label)
        raise


class GrowwAPIProxy:
    """Transparent proxy — wraps every GrowwAPI method with groww_call."""

    def __init__(self, client):
        object.__setattr__(self, '_client', client)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, '_client'), name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def wrapped(*args, **kwargs):
            return groww_call(name, attr, *args, **kwargs)

        return wrapped

    def __setattr__(self, name, value):
        setattr(self._client, name, value)
