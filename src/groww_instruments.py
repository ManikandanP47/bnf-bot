"""Resolve Groww exchange_token for WebSocket feed subscriptions."""

import threading

_lock = threading.Lock()
_token_cache: dict = {}  # groww_symbol -> instrument dict
_bnf_token: str = ''

BNF_GROWW_SYMBOLS = ('NSE_BANKNIFTY', 'NSE-BANKNIFTY', 'BANKNIFTY')


def _feed_instrument(inst: dict) -> dict:
    return {
        'exchange': inst.get('exchange', 'NSE'),
        'segment': inst.get('segment', 'CASH'),
        'exchange_token': str(inst.get('exchange_token', '')),
    }


def resolve_groww_symbol(groww, groww_symbol: str, segment: str = '') -> dict:
    """Lookup instrument dict for GrowwFeed.subscribe_ltp."""
    with _lock:
        if groww_symbol in _token_cache:
            return _token_cache[groww_symbol]

    inst = None
    try:
        inst = groww.get_instrument_by_groww_symbol(groww_symbol)
    except Exception:
        pass

    if not inst and segment:
        try:
            sym = groww_symbol.replace('NSE_', '').replace('NSE-', '')
            inst = groww.get_instrument_by_exchange_and_trading_symbol(
                exchange='NSE', trading_symbol=sym,
            )
        except Exception:
            pass

    if not inst:
        return {}

    out = _feed_instrument(inst)
    if out.get('exchange_token'):
        with _lock:
            _token_cache[groww_symbol] = out
            _token_cache[out['exchange_token']] = {
                **out, 'groww_symbol': groww_symbol,
            }
    return out


def bnf_feed_instrument(groww) -> dict:
    """BankNifty index instrument for live feed."""
    global _bnf_token
    if _bnf_token:
        return {'exchange': 'NSE', 'segment': 'CASH', 'exchange_token': _bnf_token}

    for sym in BNF_GROWW_SYMBOLS:
        inst = resolve_groww_symbol(groww, sym, segment='CASH')
        if inst.get('exchange_token'):
            _bnf_token = inst['exchange_token']
            return inst
    return {}


def option_feed_instrument(groww, strike: int, opt_type: str, expiry: str) -> dict:
    from src.groww_symbols import groww_option_symbol
    sym = groww_option_symbol('BANKNIFTY', strike, opt_type, expiry)
    return resolve_groww_symbol(groww, sym, segment='FNO')


def get_bnf_exchange_token() -> str:
    return _bnf_token


def symbol_for_token(exchange_token: str) -> str:
    with _lock:
        row = _token_cache.get(str(exchange_token), {})
    return row.get('groww_symbol', '')
