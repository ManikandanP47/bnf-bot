"""
Option premium feed — Groww LTP when available, delta estimate fallback.
"""

import os
from core.shared_state import STATE


def _groww_token() -> str:
    token = STATE.get('system.groww_token', '')
    if token:
        return token
    return os.getenv('GROWW_ACCESS_TOKEN', '')


def fetch_option_ltp(strike: int, opt_type: str, expiry: str) -> float:
    """Get live option premium from Groww. Returns 0 if unavailable."""
    token = _groww_token()
    if not token or not strike:
        return 0.0
    try:
        from datetime import datetime
        from growwapi import GrowwAPI
        dt = datetime.strptime(expiry, '%d %b %Y')
        exp_code = dt.strftime('%y%m%d')
        symbol = f"BANKNIFTY{exp_code}{strike}{opt_type}"
        groww = GrowwAPI(token)
        q = groww.get_ltp(
            exchange_trading_symbols=(symbol,),
            segment=groww.SEGMENT_FNO,
        )
        if q and isinstance(q, dict):
            if symbol in q:
                return float(q[symbol])
            ltps = q.get('ltps', [])
            if ltps:
                return float(ltps[0].get('ltp', 0) or ltps[0].get('last_price', 0))
    except Exception:
        pass
    return 0.0


def estimate_premium(entry_prem: float, bnf_entry: float,
                     bnf_current: float, strike: float,
                     opt_type: str = 'CE') -> float:
    """Delta-based estimate when option LTP unavailable."""
    bnf_move = bnf_current - bnf_entry
    otm_gap  = abs(strike - bnf_entry) if strike and bnf_entry else 300
    if   otm_gap > 500: delta = 0.20
    elif otm_gap > 300: delta = 0.28
    elif otm_gap > 150: delta = 0.38
    else:               delta = 0.50
    if opt_type == 'PE':
        bnf_move = -bnf_move
    est = round(entry_prem + bnf_move * delta, 1)
    return max(est, 5.0)


def get_position_premium(position: dict, bnf_current: float) -> float:
    """Best available premium: Groww LTP → delta estimate."""
    zone   = STATE.get('zone', {})
    strike = position.get('strike') or zone.get('strike', 0)
    expiry = zone.get('expiry', '')
    otype  = position.get('opt_type') or zone.get('opt_type', 'CE')
    entry  = position.get('entry_price', 0)
    bnf_e  = position.get('bnf_at_entry', bnf_current)

    if strike and expiry:
        live = fetch_option_ltp(strike, otype, expiry)
        if live > 0:
            return live

    return estimate_premium(entry, bnf_e, bnf_current, strike, otype)
