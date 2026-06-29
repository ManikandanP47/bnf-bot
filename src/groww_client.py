"""Shared Groww API client — reuse one instance per token (less SDK init noise)."""

import os
from typing import Optional

_client = None
_cached_token: Optional[str] = None


def get_groww_client(token: str = ''):
    """Return a cached GrowwAPI for the current access token."""
    global _client, _cached_token
    from growwapi import GrowwAPI
    from core.shared_state import STATE

    tok = (
        token
        or STATE.get('system.groww_token', '')
        or os.getenv('GROWW_ACCESS_TOKEN', '')
    ).strip()
    if not tok:
        raise ValueError('No Groww access token')

    if _client is not None and _cached_token == tok:
        return _client

    _client = GrowwAPI(tok)
    _cached_token = tok
    return _client


def clear_groww_client():
    """Call after token refresh so the next request uses a new client."""
    global _client, _cached_token
    _client = None
    _cached_token = None
