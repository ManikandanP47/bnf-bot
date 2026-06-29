"""
Thread-safe store for Groww WebSocket LTP ticks.
Feeds virtual + real position monitoring before REST polling.
"""

import os
import time
import threading
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')

_lock = threading.Lock()
_ltp_by_symbol: dict = {}
_ltp_by_token: dict = {}
_bnf_price: float = 0.0
_connected: bool = False
_last_tick: float = 0.0
_last_watch: float = 0.0
_tick_count: int = 0

FEED_STALE_SEC = int(os.getenv('GROWW_FEED_STALE_SEC', '30'))
WATCH_DEBOUNCE_SEC = float(os.getenv('GROWW_FEED_WATCH_DEBOUNCE', '0.5'))


def _extract_ltp(node) -> float:
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, dict):
        return float(node.get('ltp') or node.get('last_price') or node.get('price') or 0)
    return 0.0


def ingest_ltp_payload(payload: dict) -> int:
    """Parse GrowwFeed.get_ltp() — LIVE_DATA / NSE / segment / token."""
    global _bnf_price, _connected, _last_tick, _tick_count

    if not payload or not isinstance(payload, dict):
        return 0

    live = payload.get('LIVE_DATA', payload)
    if not isinstance(live, dict):
        return 0

    from src.groww_instruments import get_bnf_exchange_token, symbol_for_token

    updated = 0
    now = time.time()
    bnf_tok = get_bnf_exchange_token()

    for _exchange, segmap in live.items():
        if not isinstance(segmap, dict):
            continue
        for _segment, tokens in segmap.items():
            if not isinstance(tokens, dict):
                continue
            for token, data in tokens.items():
                ltp = _extract_ltp(data)
                if ltp <= 0:
                    continue
                sym = symbol_for_token(str(token))
                with _lock:
                    _ltp_by_token[str(token)] = ltp
                    if sym:
                        _ltp_by_symbol[sym] = {'ltp': ltp, 'ts': now}
                    if bnf_tok and str(token) == str(bnf_tok):
                        _bnf_price = ltp
                updated += 1

    with _lock:
        if updated > 0:
            _connected = True
            _last_tick = now
            _tick_count += updated

    if _bnf_price > 0:
        _apply_bnf_to_state(_bnf_price)

    return updated


def _apply_bnf_to_state(price: float):
    from core.shared_state import STATE
    STATE.set('market.price', price)
    STATE.set('market.data_source', 'GROWW_FEED')
    STATE.set('market.connected', True)
    STATE.set('market.updated_at', datetime.now(IST).strftime('%H:%M:%S'))
    STATE.set('system.groww_feed_last_tick', datetime.now(IST).strftime('%H:%M:%S'))


def get_feed_ltp(groww_symbol: str) -> float:
    with _lock:
        row = _ltp_by_symbol.get(groww_symbol, {})
        if row and time.time() - row.get('ts', 0) <= FEED_STALE_SEC:
            return row.get('ltp', 0.0)
    return 0.0


def is_feed_live() -> bool:
    with _lock:
        return _connected and (time.time() - _last_tick) <= FEED_STALE_SEC


def mark_disconnected():
    global _connected
    with _lock:
        _connected = False


def feed_status() -> dict:
    with _lock:
        return {
            'live': _connected and (time.time() - _last_tick) <= FEED_STALE_SEC,
            'last_tick_ago': int(time.time() - _last_tick) if _last_tick else -1,
            'tick_count': _tick_count,
            'bnf': _bnf_price,
            'symbols': len(_ltp_by_symbol),
        }


def run_position_watch():
    global _last_watch
    now = time.time()
    if now - _last_watch < WATCH_DEBOUNCE_SEC:
        return
    _last_watch = now
    try:
        from src.position_watch import watch_mode_active
        if not watch_mode_active():
            return
        from src.shadow_learning import has_open_virtual_orders, tick_shadow_trades
        if has_open_virtual_orders():
            tick_shadow_trades()
    except Exception:
        pass


def format_feed_status_line() -> str:
    s = feed_status()
    if s['live']:
        return (
            f"📡 *Groww Feed LIVE* — BNF {s['bnf']:,.0f} | "
            f"{s['symbols']} symbols | tick {s['last_tick_ago']}s ago"
        )
    if s['last_tick_ago'] >= 0:
        return f"📡 Groww Feed stale ({s['last_tick_ago']}s) — REST fallback"
    return '📡 Groww Feed off — REST polling'
