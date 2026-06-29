"""
Strike Picker — find options that fit ₹5k capital
Scans OTM strikes via Groww LTP until lot cost is affordable.
"""

import os
from src.premium_feed import fetch_option_ltp

LOT_SIZE = 15
STRIKE_GAP = 100


def _max_lot_cost() -> float:
    capital = float(os.getenv('LIVE_CAPITAL_RS', '5000'))
    return capital * 0.80


def find_affordable_strike(bnf_price: float, bias: str,
                           expiry: str) -> dict:
    """
    If default strike costs > 80% of capital, scan further OTM strikes.
    Picks the highest premium still within budget (better delta, still affordable).
    Returns None if nothing fits.
    """
    if not bnf_price or not expiry:
        return None

    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    max_cost = _max_lot_cost()
    opt_type = 'CE' if bias == 'BULLISH' else 'PE'

    if bias == 'BULLISH':
        candidates = [atm + STRIKE_GAP * i for i in range(1, 30)]
    else:
        candidates = [atm - STRIKE_GAP * i for i in range(1, 30)]

    best = None
    for strike in candidates:
        if strike <= 0:
            continue
        ltp = fetch_option_ltp(strike, opt_type, expiry)
        if ltp <= 0:
            continue
        cost = ltp * LOT_SIZE
        if cost > max_cost:
            continue
        # Prefer higher premium within budget = more delta, still affordable
        if not best or ltp > best['premium']:
            sl_pct = 0.30
            tgt_mul = 2.0
            best = {
                'strike':     strike,
                'opt_type':   opt_type,
                'name':       f'BANKNIFTY {strike} {opt_type}',
                'premium':    round(ltp, 0),
                'sl_prem':    round(ltp * (1 - sl_pct), 0),
                'tgt_prem':   round(ltp * tgt_mul, 0),
                'lot_cost':   round(cost, 0),
                'max_loss':   round(ltp * sl_pct * LOT_SIZE, 0),
                'max_gain':   round(ltp * (tgt_mul - 1) * LOT_SIZE, 0),
                'expiry':     expiry,
                'otm_steps':  abs(strike - atm) // STRIKE_GAP,
            }

    return best


def format_strike_switch(old: dict, new: dict, capital: float) -> str:
    return (
        f"💡 *Strike adjusted for ₹{capital:,.0f} capital*\n"
        f"Was: {old.get('name', '?')} (~₹{old.get('premium', 0)}/unit)\n"
        f"Now: *{new['name']}* @ ₹{new['premium']}/unit\n"
        f"Lot cost: ₹{new['lot_cost']:,} | Max loss: ₹{new['max_loss']:,}\n"
        f"Min profit target (leg 1): ~₹{round((new['premium']*1.5-new['premium'])*7):,}"
    )
