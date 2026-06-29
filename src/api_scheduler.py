"""
Central API throttle — one clock for Groww, NSE OI, VIX, context refreshes.
Reduces duplicate calls across agents and commands.
"""

import time

# Min seconds between identical API families
TTL = {
    'groww_ltp':        10,
    'groww_historical': 300,
    'groww_option_ltp': 10,
    'nse_oi':           300,
    'vix':              300,
    'market_context':   600,
    'market_flow':      300,
    'backtest':         3600,
}

_last: dict = {}


def should_fetch(key: str, ttl_sec: int = None) -> bool:
    ttl = ttl_sec if ttl_sec is not None else TTL.get(key, 60)
    last = _last.get(key, 0)
    return time.time() - last >= ttl


def mark_fetched(key: str):
    _last[key] = time.time()


def seconds_until(key: str, ttl_sec: int = None) -> int:
    ttl = ttl_sec if ttl_sec is not None else TTL.get(key, 60)
    return max(0, int(ttl - (time.time() - _last.get(key, 0))))


def format_scheduler_status() -> str:
    lines = ['*API throttle:*']
    for k in ('groww_ltp', 'nse_oi', 'vix', 'market_flow'):
        left = seconds_until(k)
        lines.append(f'  {k}: next in {left}s')
    return '\n'.join(lines)
