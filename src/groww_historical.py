"""
Groww historical candles — BankNifty index OHLC (no yfinance).
Requires Groww Trade API subscription for historical data.
"""

import os
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
BANKNIFTY_GROWW_SYMBOLS = ('NSE-BANKNIFTY', 'NSE_BANKNIFTY')


def _get_groww(token: str = ''):
    from src.groww_client import get_groww_client
    tok = token or os.getenv('GROWW_ACCESS_TOKEN', '')
    if not tok:
        from core.shared_state import STATE
        tok = STATE.get('system.groww_token', '')
    if not tok:
        from src.groww_auth import fetch_groww_token
        tok = fetch_groww_token()
    return get_groww_client(tok), tok


def _interval_const(groww, minutes: int):
    mapping = {
        1:  'CANDLE_INTERVAL_MIN_1',
        5:  'CANDLE_INTERVAL_MIN_5',
        15: 'CANDLE_INTERVAL_MIN_15',
        30: 'CANDLE_INTERVAL_MIN_30',
        60: 'CANDLE_INTERVAL_MIN_60',
    }
    name = mapping.get(minutes, 'CANDLE_INTERVAL_MIN_1')
    return getattr(groww, name, groww.CANDLE_INTERVAL_MIN_1)


def fetch_banknifty_candles(interval_min: int = 1,
                            lookback_hours: int = 8,
                            token: str = '') -> list:
    """
    Fetch BankNifty index OHLC from Groww.
    Returns list of dicts: open, high, low, close, volume, time, ts
    """
    try:
        groww, _ = _get_groww(token)
    except Exception:
        return []

    end   = datetime.now(IST)
    start = end - timedelta(hours=lookback_hours)
    interval = _interval_const(groww, interval_min)

    for sym in BANKNIFTY_GROWW_SYMBOLS:
        try:
            resp = groww.get_historical_candles(
                exchange=groww.EXCHANGE_NSE,
                segment=groww.SEGMENT_CASH,
                groww_symbol=sym,
                start_time=start.strftime('%Y-%m-%d %H:%M:%S'),
                end_time=end.strftime('%Y-%m-%d %H:%M:%S'),
                candle_interval=interval,
            )
            raw = resp.get('candles', []) if isinstance(resp, dict) else []
            if not raw:
                continue
            out = []
            for c in raw:
                if len(c) < 6:
                    continue
                ts_str = str(c[0]).replace('T', ' ')
                try:
                    ts = datetime.strptime(ts_str[:19], '%Y-%m-%d %H:%M:%S')
                    ts = IST.localize(ts) if ts.tzinfo is None else ts.astimezone(IST)
                except Exception:
                    continue
                out.append({
                    'open':   float(c[1]),
                    'high':   float(c[2]),
                    'low':    float(c[3]),
                    'close':  float(c[4]),
                    'volume': int(c[5] or 0),
                    'time':   ts.strftime('%H:%M'),
                    'ts':     ts,
                })
            if out:
                return out
        except Exception:
            continue
    return []


def seed_candle_builders(b1, b5, b15, token: str = '') -> int:
    """Load today's 1m candles into builders for instant 5m/15m on cold start."""
    candles = fetch_banknifty_candles(interval_min=1, lookback_hours=10, token=token)
    if not candles:
        return 0
    today = datetime.now(IST).date()
    count = 0
    for c in candles:
        if c['ts'].date() != today:
            continue
        vol = max(c['volume'], 1)
        b1.add_tick(c['close'], vol, c['ts'])
        count += 1
    return count


def fetch_latest_price(token: str = '') -> float:
    """Last close from Groww 1m candle if LTP fails."""
    candles = fetch_banknifty_candles(interval_min=1, lookback_hours=2, token=token)
    if candles:
        return float(candles[-1]['close'])
    return 0.0
