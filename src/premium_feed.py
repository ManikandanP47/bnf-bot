"""
Option premium — Groww API is the source of truth for virtual orders.

Virtual orders (memory only, no Groww buy/sell):
  • ENTRY  → Groww option LTP at that second = virtual fill price
  • EVERY TICK → Groww option LTP again = real mark-to-market P&L
  • Delta model used ONLY when Groww LTP is unavailable (API down / rate limit)

No mirror. Same API you would use for a real order — we just never call place_order.
"""

import os
import time
from core.shared_state import STATE

# Per-symbol LTP cache — short TTL for ~10s live virtual monitoring
_LTP_CACHE: dict = {}  # symbol -> (ltp, timestamp)
_LTP_CACHE_TTL = int(os.getenv('GROWW_LTP_CACHE_SEC', '10'))
VIRTUAL_REQUIRE_GROWW = os.getenv('VIRTUAL_REQUIRE_GROWW_LTP', 'true').lower() == 'true'


def _groww_token() -> str:
    token = STATE.get('system.groww_token', '')
    if token:
        return token
    return os.getenv('GROWW_ACCESS_TOKEN', '')


def _option_symbol(strike: int, opt_type: str, expiry: str) -> str:
    from src.groww_symbols import groww_option_symbol
    return groww_option_symbol('BANKNIFTY', strike, opt_type, expiry)


def _cached_ltp(symbol: str, max_age_sec: int = None) -> float:
    if symbol not in _LTP_CACHE:
        return 0.0
    ltp, ts = _LTP_CACHE[symbol]
    age = time.time() - ts
    limit = max_age_sec if max_age_sec is not None else _LTP_CACHE_TTL
    return ltp if age <= limit else 0.0


def _store_ltp(symbol: str, ltp: float):
    if ltp > 0:
        _LTP_CACHE[symbol] = (ltp, time.time())


def fetch_option_ltp(strike: int, opt_type: str, expiry: str,
                     force_fresh: bool = False) -> float:
    """Groww option LTP. Returns 0 if unavailable."""
    r = fetch_virtual_ltp(strike, opt_type, expiry, force_fresh=force_fresh)
    return r.get('ltp', 0.0)


def fetch_virtual_ltp(strike: int, opt_type: str, expiry: str,
                      force_fresh: bool = False) -> dict:
    """
    Virtual order pricing from Groww — same API as a real order would use.

    Returns:
        ltp, source (GROWW_LTP | STALE_GROWW_LTP | UNAVAILABLE), symbol
    """
    if not strike or not expiry:
        return {'ltp': 0.0, 'source': 'UNAVAILABLE', 'symbol': ''}

    symbol = _option_symbol(strike, opt_type, expiry)

    try:
        from src.groww_feed_store import get_feed_ltp, is_feed_live
        if is_feed_live():
            fl = get_feed_ltp(symbol)
            if fl > 0:
                _store_ltp(symbol, fl)
                return {'ltp': fl, 'source': 'GROWW_FEED', 'symbol': symbol}
    except Exception:
        pass

    if not force_fresh:
        cached = _cached_ltp(symbol)
        if cached > 0:
            return {'ltp': cached, 'source': 'GROWW_LTP', 'symbol': symbol}

    token = _groww_token()
    if not token:
        stale = _cached_ltp(symbol, max_age_sec=180)
        if stale > 0:
            return {'ltp': stale, 'source': 'STALE_GROWW_LTP', 'symbol': symbol}
        return {'ltp': 0.0, 'source': 'UNAVAILABLE', 'symbol': symbol}

    try:
        from src.api_scheduler import should_fetch, mark_fetched
        cache_key = f'opt_ltp:{symbol}'
        if not force_fresh and not should_fetch(cache_key, _LTP_CACHE_TTL):
            cached = _cached_ltp(symbol, max_age_sec=_LTP_CACHE_TTL + 5)
            if cached > 0:
                return {'ltp': cached, 'source': 'GROWW_LTP', 'symbol': symbol}

        from src.groww_client import get_groww_client
        groww = get_groww_client(token)
        q = groww.get_ltp(
            exchange_trading_symbols=(symbol,),
            segment=groww.SEGMENT_FNO,
        )
        ltp = 0.0
        if q and isinstance(q, dict):
            if symbol in q:
                ltp = float(q[symbol])
            else:
                ltps = q.get('ltps', [])
                if ltps:
                    ltp = float(ltps[0].get('ltp', 0) or ltps[0].get('last_price', 0))

        if ltp > 0:
            _store_ltp(symbol, ltp)
            mark_fetched(cache_key)
            return {'ltp': ltp, 'source': 'GROWW_LTP', 'symbol': symbol}

    except Exception as e:
        STATE.add_error(f"Groww LTP {strike}{opt_type}: {str(e)[:30]}")

    stale = _cached_ltp(symbol, max_age_sec=180)
    if stale > 0:
        return {'ltp': stale, 'source': 'STALE_GROWW_LTP', 'symbol': symbol}

    return {'ltp': 0.0, 'source': 'UNAVAILABLE', 'symbol': symbol}


def virtual_buy_fill(strike: int, opt_type: str, expiry: str) -> dict:
    """
    Virtual BUY — lock entry at live Groww LTP + live-like friction (spread + slip).
    Same WebSocket tape as a real market buy; fill price is pessimistic vs raw LTP.
    """
    q = fetch_virtual_ltp(strike, opt_type, expiry, force_fresh=True)
    if q['ltp'] <= 0:
        return {
            'ok': False,
            'reason': 'Groww LTP unavailable — cannot open virtual order at real premium',
            **q,
        }
    from src.trade_analytics import virtual_buy_fill_price
    buy = virtual_buy_fill_price(q['ltp'])
    return {
        'ok': True,
        'premium': buy['fill'],
        'ltp': q['ltp'],
        'entry_friction': buy['friction'],
        'live_like': buy['live_like'],
        'prem_source': q['source'],
        'symbol': q['symbol'],
        'reason': '',
    }


def virtual_mark_to_market(position: dict, bnf_current: float = 0) -> dict:
    """
    Virtual position P&L — re-fetch Groww LTP for this exact strike/expiry.
    P&L = (current Groww LTP − entry premium) × 15
    """
    strike = position.get('strike', 0)
    expiry = position.get('expiry', '')
    otype = position.get('opt_type', 'CE')
    entry = position.get('entry_price', 0)

    if not strike or not expiry:
        return _fallback_mtm(position, bnf_current, 'UNAVAILABLE')

    q = fetch_virtual_ltp(strike, otype, expiry, force_fresh=True)

    if q['ltp'] > 0:
        prem = q['ltp']
        pnl = round((prem - entry) * 15, 0) if entry else 0
        return {
            'premium': prem,
            'pnl_rs': pnl,
            'prem_source': q['source'],
            'symbol': q['symbol'],
            'is_real': q['source'] in ('GROWW_LTP', 'STALE_GROWW_LTP'),
        }

    return _fallback_mtm(position, bnf_current, 'DELTA_FALLBACK')


def _fallback_mtm(position: dict, bnf_current: float, source: str) -> dict:
    """Only when Groww LTP completely unavailable."""
    entry = position.get('entry_price', 0)
    bnf_e = position.get('bnf_at_entry', bnf_current)
    strike = position.get('strike', 0)
    otype = position.get('opt_type', 'CE')
    est = estimate_premium(entry, bnf_e, bnf_current, strike, otype)
    pnl = round((est - entry) * 15, 0) if entry else 0
    return {
        'premium': est,
        'pnl_rs': pnl,
        'prem_source': source,
        'symbol': '',
        'is_real': False,
    }


def estimate_premium(entry_prem: float, bnf_entry: float,
                     bnf_current: float, strike: float,
                     opt_type: str = 'CE') -> float:
    """Last resort when Groww LTP cannot be fetched at all."""
    bnf_move = bnf_current - bnf_entry
    otm_gap = abs(strike - bnf_entry) if strike and bnf_entry else 300
    if otm_gap > 500:
        delta = 0.20
    elif otm_gap > 300:
        delta = 0.28
    elif otm_gap > 150:
        delta = 0.38
    else:
        delta = 0.50
    if opt_type == 'PE':
        bnf_move = -bnf_move
    est = round(entry_prem + bnf_move * delta, 1)
    return max(est, 5.0)


def get_position_premium(position: dict, bnf_current: float) -> float:
    """Best MTM — active watch uses hybrid Groww + BNF anchor."""
    try:
        from src.position_watch import watch_mode_active, smart_mark_to_market
        if watch_mode_active():
            return smart_mark_to_market(position, bnf_current)['premium']
    except Exception:
        pass
    return virtual_mark_to_market(position, bnf_current)['premium']


def resolve_premium(strike: int, opt_type: str, expiry: str,
                    entry_prem: float, bnf_entry: float,
                    bnf_current: float) -> tuple:
    q = virtual_mark_to_market({
        'strike': strike, 'opt_type': opt_type, 'expiry': expiry,
        'entry_price': entry_prem, 'bnf_at_entry': bnf_entry,
    }, bnf_current)
    src = q['prem_source']
    if src == 'DELTA_FALLBACK':
        src = 'DELTA_MODEL'
    return q['premium'], src
