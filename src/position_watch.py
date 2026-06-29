"""
Active Position Watch — same monitor for virtual AND real orders.

Problem: fixed 10s polling misses fast F&O moves.
Solution: hybrid watch mode when any position is open:

  1. BNF polled faster (5s) while position open
  2. Between Groww option LTP calls → instant estimate from last real LTP + BNF move
  3. Refresh Groww option LTP when:
       • BNF moves ≥ N points since last anchor
       • Premium estimate nears SL or target
       • Anchor older than max age (safety refresh)

Groww REST has WebSocket feed (`GrowwFeedAgent`) — this module bridges
between feed ticks and REST refreshes when a position is open:
"""

import os
import time
from core.shared_state import STATE

BNF_MOVE_REFRESH = float(os.getenv('BNF_MOVE_REFRESH', '12'))
ANCHOR_MAX_AGE_SEC = int(os.getenv('ANCHOR_MAX_AGE_SEC', '40'))
MIN_OPT_LTP_SEC = int(os.getenv('MIN_OPT_LTP_SEC', '3'))
NEAR_EXIT_PCT = float(os.getenv('NEAR_EXIT_PCT', '0.08'))

# symbol -> {ltp, bnf, ts, strike, opt_type}
_anchors: dict = {}
_last_api: dict = {}  # symbol -> timestamp


def watch_mode_active() -> bool:
    """True when virtual OR real position needs live monitoring."""
    if STATE.get('position.open'):
        return True
    try:
        from src.shadow_learning import has_open_virtual_orders
        return has_open_virtual_orders()
    except Exception:
        return False


def bnf_poll_interval_sec() -> int:
    """Faster BNF when watching an open position."""
    if watch_mode_active():
        return int(os.getenv('POSITION_WATCH_BNF_SEC', '5'))
    return int(os.getenv('BNF_POLL_IDLE_SEC', '10'))


def clear_anchor(symbol: str = ''):
    if symbol:
        _anchors.pop(symbol, None)
    else:
        _anchors.clear()


def _delta_for(strike: float, bnf: float, opt_type: str) -> float:
    otm = abs(strike - bnf) if strike and bnf else 300
    if otm > 500:
        d = 0.20
    elif otm > 300:
        d = 0.28
    elif otm > 150:
        d = 0.38
    else:
        d = 0.50
    return d


def _interpolate_from_anchor(anchor: dict, bnf: float, opt_type: str) -> float:
    """Instant premium from last Groww LTP + BNF move (no API wait)."""
    ltp_a = anchor.get('ltp', 0)
    bnf_a = anchor.get('bnf', bnf)
    strike = anchor.get('strike', 0)
    if ltp_a <= 0:
        return 0.0
    move = bnf - bnf_a
    if opt_type == 'PE':
        move = -move
    delta = _delta_for(strike, bnf, opt_type)
    return max(round(ltp_a + move * delta, 1), 5.0)


def _near_exit(est_prem: float, position: dict) -> bool:
    sl = position.get('sl_prem') or position.get('sl_from_initial', 0)
    tgt = position.get('tgt_prem') or position.get('target_prem', 0)
    if sl and est_prem <= sl * (1 + NEAR_EXIT_PCT):
        return True
    if tgt and est_prem >= tgt * (1 - NEAR_EXIT_PCT):
        return True
    return False


def _can_fetch_ltp(symbol: str) -> bool:
    last = _last_api.get(symbol, 0)
    return time.time() - last >= MIN_OPT_LTP_SEC


def _set_anchor(symbol: str, ltp: float, bnf: float, strike: int, opt_type: str):
    _anchors[symbol] = {
        'ltp': ltp, 'bnf': bnf, 'ts': time.time(),
        'strike': strike, 'opt_type': opt_type,
    }
    _last_api[symbol] = time.time()


def smart_mark_to_market(position: dict, bnf_current: float = 0) -> dict:
    """
    Hybrid MTM for virtual + real positions.
    Groww LTP on triggers; instant BNF-anchored estimate between refreshes.
    """
    from src.premium_feed import fetch_virtual_ltp, estimate_premium

    strike = position.get('strike', 0)
    expiry = position.get('expiry', '')
    otype = position.get('opt_type', 'CE')
    entry = position.get('entry_price', 0)
    bnf = bnf_current or STATE.get('market.price', 0)
    bnf_e = position.get('bnf_at_entry', bnf)

    if not strike or not expiry:
        est = estimate_premium(entry, bnf_e, bnf, strike, otype)
        return {
            'premium': est, 'pnl_rs': round((est - entry) * 15, 0) if entry else 0,
            'prem_source': 'DELTA_FALLBACK', 'is_real': False, 'symbol': '',
        }

    from src.premium_feed import _option_symbol
    symbol = _option_symbol(strike, otype, expiry)
    anchor = _anchors.get(symbol, {})
    est_anchor = _interpolate_from_anchor(anchor, bnf, otype) if anchor else 0

    bnf_anchor = anchor.get('bnf', bnf_e)
    bnf_move = abs(bnf - bnf_anchor) if bnf and bnf_anchor else 0
    anchor_age = time.time() - anchor.get('ts', 0) if anchor else 9999

    need_refresh = (
        not anchor
        or bnf_move >= BNF_MOVE_REFRESH
        or anchor_age >= ANCHOR_MAX_AGE_SEC
        or (est_anchor > 0 and _near_exit(est_anchor, position))
    )

    if need_refresh and _can_fetch_ltp(symbol):
        q = fetch_virtual_ltp(strike, otype, expiry, force_fresh=True)
        if q.get('ltp', 0) > 0:
            _set_anchor(symbol, q['ltp'], bnf, strike, otype)
            prem = q['ltp']
            return {
                'premium': prem,
                'pnl_rs': round((prem - entry) * 15, 0) if entry else 0,
                'prem_source': 'GROWW_LTP',
                'symbol': symbol,
                'is_real': True,
            }

    if est_anchor > 0:
        return {
            'premium': est_anchor,
            'pnl_rs': round((est_anchor - entry) * 15, 0) if entry else 0,
            'prem_source': 'LIVE_ANCHOR',
            'symbol': symbol,
            'is_real': True,
        }

    q = fetch_virtual_ltp(strike, otype, expiry, force_fresh=not anchor)
    if q.get('ltp', 0) > 0:
        _set_anchor(symbol, q['ltp'], bnf, strike, otype)
        prem = q['ltp']
        return {
            'premium': prem,
            'pnl_rs': round((prem - entry) * 15, 0) if entry else 0,
            'prem_source': q.get('source', 'GROWW_LTP'),
            'symbol': symbol,
            'is_real': True,
        }

    est = estimate_premium(entry, bnf_e, bnf, strike, otype)
    return {
        'premium': est,
        'pnl_rs': round((est - entry) * 15, 0) if entry else 0,
        'prem_source': 'DELTA_FALLBACK',
        'symbol': symbol,
        'is_real': False,
    }


def format_watch_mode_status() -> str:
    active = watch_mode_active()
    if not active:
        return '👁️ Watch: idle (no open position)'
    return (
        f'👁️ *Active watch ON* — BNF every {bnf_poll_interval_sec()}s\n'
        f'  Option LTP: Groww on BNF ±{BNF_MOVE_REFRESH:.0f}pt / near SL-TGT / {ANCHOR_MAX_AGE_SEC}s\n'
        f'  Between refreshes: instant estimate from last real LTP + BNF move'
    )
