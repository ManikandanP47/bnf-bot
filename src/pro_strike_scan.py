"""
Pro strike ladder — scan multiple CE/PE strikes like an experienced F&O trader.

Scores OTM ladder on the bias side (+ optional CE vs PE compare for training logs).
Used in virtual sim with pro training capital (₹25k+).
"""

import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
LOT_SIZE = 15
STRIKE_GAP = 100

PRO_STRIKE_SCAN = os.getenv('PRO_STRIKE_SCAN', 'true').lower() == 'true'
PRO_TRAINING_MODE = os.getenv('PRO_TRAINING_MODE', 'true').lower() == 'true'
LADDER_DEPTH = int(os.getenv('PRO_LADDER_DEPTH', '12'))
SWEET_MIN = float(os.getenv('SWEET_PREMIUM_MIN', '120'))
COMPARE_CE_PE = os.getenv('PRO_COMPARE_CE_PE', 'true').lower() == 'true'


def _effective_sweet_max() -> float:
    pro_max = float(os.getenv('PRO_SWEET_PREMIUM_MAX', '450'))
    if os.getenv('SIM_REQUIRE_SWEET_PREMIUM', 'true').lower() == 'true':
        try:
            from src.wr_filters import SWEET_PREMIUM_MAX
            return min(pro_max, SWEET_PREMIUM_MAX)
        except Exception:
            pass
    return pro_max


SWEET_MAX = _effective_sweet_max()


def _max_lot_cost(capital: float = None) -> float:
    if capital is None:
        try:
            from src.sim_wallet import wallet_core
            if PRO_TRAINING_MODE:
                capital = wallet_core().get('available') or wallet_core().get('balance', 25000)
            else:
                from src.capital_guard import LIVE_CAPITAL_RS
                capital = LIVE_CAPITAL_RS
        except Exception:
            capital = 25000 if PRO_TRAINING_MODE else 5000
    return float(capital) * 0.45  # pro: max ~45% per leg (room for 2–3 legs)


def _score_strike(strike: int, opt_type: str, ltp: float, bnf_price: float,
                  bias: str, lot_cost: float, max_cost: float) -> dict:
    """Higher score = better pro-style pick."""
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    otm_steps = abs(strike - atm) // STRIKE_GAP
    score = 0
    reasons = []

    # Sweet premium band (wider in pro training)
    if SWEET_MIN <= ltp <= SWEET_MAX:
        score += 3
        reasons.append(f'sweet ₹{ltp:.0f}')
    elif ltp < SWEET_MIN:
        score += 1
        reasons.append('cheap OTM')
    else:
        score -= 1
        reasons.append('expensive')

    # Delta proxy: 1–3 OTM steps ideal for BNF buyers
    if 1 <= otm_steps <= 3:
        score += 3
        reasons.append(f'{otm_steps} OTM')
    elif otm_steps == 0:
        score += 1
        reasons.append('ATM-ish')
    elif otm_steps <= 5:
        score += 1
        reasons.append(f'{otm_steps} OTM deep')
    else:
        score -= 1
        reasons.append('lottery strike')

    # Affordability — prefer using capital efficiently
    if lot_cost <= max_cost * 0.35:
        score += 1
        reasons.append('light on wallet')
    elif lot_cost <= max_cost:
        score += 2
        reasons.append('fits budget')
    else:
        return None

    # IV rank adjustment from cached chain
    try:
        from core.shared_state import STATE
        iv_rank = (STATE.get('market.option_chain') or {}).get('iv_rank', 50)
        if iv_rank < 40:
            score += 1
            reasons.append(f'IV rank {iv_rank:.0f} low')
        elif iv_rank > 75:
            score -= 1
            reasons.append(f'IV rank {iv_rank:.0f} high')
    except Exception:
        pass

    sl_pct = 0.28 if PRO_TRAINING_MODE else 0.30
    tgt_mul = 2.0
    return {
        'strike': strike,
        'opt_type': opt_type,
        'name': f'BANKNIFTY {strike} {opt_type}',
        'premium': round(ltp, 0),
        'sl_prem': round(ltp * (1 - sl_pct), 0),
        'tgt_prem': round(ltp * tgt_mul, 0),
        'lot_cost': round(lot_cost, 0),
        'max_loss': round(ltp * sl_pct * LOT_SIZE, 0),
        'max_gain': round(ltp * (tgt_mul - 1) * LOT_SIZE, 0),
        'otm_steps': otm_steps,
        'score': score,
        'reasons': reasons,
        'reason_txt': ', '.join(reasons),
    }


def scan_strike_ladder(bnf_price: float, bias: str, expiry: str,
                       capital: float = None) -> dict:
    """
    Scan OTM ladder on bias side. Returns ranked candidates + best pick.
    """
    from src.premium_feed import fetch_option_ltp

    if not bnf_price or not expiry or bias not in ('BULLISH', 'BEARISH'):
        return {'ok': False, 'reason': 'missing price/bias/expiry', 'candidates': []}

    max_cost = _max_lot_cost(capital)
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    opt_type = 'CE' if bias == 'BULLISH' else 'PE'

    if bias == 'BULLISH':
        strikes = [atm + STRIKE_GAP * i for i in range(0, LADDER_DEPTH + 1)]
    else:
        strikes = [atm - STRIKE_GAP * i for i in range(0, LADDER_DEPTH + 1)]

    candidates = []
    for strike in strikes:
        if strike <= 0:
            continue
        ltp = fetch_option_ltp(strike, opt_type, expiry)
        if ltp <= 0:
            continue
        lot_cost = ltp * LOT_SIZE
        row = _score_strike(strike, opt_type, ltp, bnf_price, bias, lot_cost, max_cost)
        if row:
            candidates.append(row)

    candidates.sort(key=lambda x: (-x['score'], -x['premium']))
    best = candidates[0] if candidates else None

    ce_pe_note = ''
    alt_side = None
    if COMPARE_CE_PE and best:
        alt_side = _compare_opposite_side(bnf_price, bias, expiry, max_cost)

    result = {
        'ok': bool(best),
        'bias': bias,
        'atm': atm,
        'expiry': expiry,
        'max_lot_cost': round(max_cost, 0),
        'candidates': candidates[:8],
        'best': best,
        'ce_pe_compare': alt_side,
        'scanned': len(candidates),
        'ts': datetime.now(IST).strftime('%H:%M:%S'),
    }

    try:
        from core.shared_state import STATE
        STATE.set('market.strike_ladder', result)
    except Exception:
        pass

    return result


def _compare_opposite_side(bnf_price: float, bias: str, expiry: str,
                           max_cost: float) -> dict:
    """Training: score one ATM-ish strike on opposite side (usually worse for bias)."""
    from src.premium_feed import fetch_option_ltp
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    opp = 'PE' if bias == 'BULLISH' else 'CE'
    strike = atm - STRIKE_GAP if opp == 'PE' else atm + STRIKE_GAP
    ltp = fetch_option_ltp(strike, opp, expiry)
    if ltp <= 0:
        return {}
    lot_cost = ltp * LOT_SIZE
    if lot_cost > max_cost:
        return {'opt_type': opp, 'strike': strike, 'note': 'too costly for hedge'}
    row = _score_strike(strike, opp, ltp, bnf_price, bias, lot_cost, max_cost)
    if not row:
        return {}
    return {
        'opt_type': opp,
        'strike': strike,
        'premium': row['premium'],
        'score': row['score'],
        'note': (
            f'{opp} score {row["score"]} vs bias {bias} — '
            f'{"hedge only" if row["score"] < 4 else "unusual alternate"}'
        ),
    }


def pick_pro_strike(bnf_price: float, bias: str, expiry: str,
                    capital: float = None) -> dict:
    """Best strike dict for sim/execute, or {} if none."""
    if not PRO_STRIKE_SCAN:
        from src.strike_picker import find_affordable_strike
        picked = find_affordable_strike(bnf_price, bias, expiry)
        return picked or {}

    try:
        from src.pro_trader_decision import PRO_CHAIN_SCAN, pick_chain_best_rr
        if PRO_CHAIN_SCAN:
            picked = pick_chain_best_rr(bnf_price, bias, expiry, capital)
            if picked:
                return picked
    except Exception:
        pass

    ladder = scan_strike_ladder(bnf_price, bias, expiry, capital)
    best = ladder.get('best')
    if not best:
        from src.strike_picker import find_affordable_strike
        picked = find_affordable_strike(bnf_price, bias, expiry)
        return picked or {}

    out = dict(best)
    out['expiry'] = expiry
    out['prem_source'] = 'PRO_LADDER'
    out['ladder_rank'] = 1
    out['ladder_scanned'] = ladder.get('scanned', 0)
    return out


def format_ladder_telegram(ladder: dict) -> str:
    if not ladder.get('ok'):
        return 'No strike ladder — missing data'
    lines = [
        f"📐 *Pro strike ladder* ({ladder.get('bias')})",
        f"ATM {ladder.get('atm'):,} | scanned {ladder.get('scanned')} | "
        f"budget ₹{ladder.get('max_lot_cost', 0):,.0f}/lot",
    ]
    for i, c in enumerate(ladder.get('candidates', [])[:5], 1):
        mark = '👉' if i == 1 else '  '
        lines.append(
            f"{mark} {i}. {c['name']} ₹{c['premium']} "
            f"score {c['score']} — {c['reason_txt']}"
        )
    cmp_ = ladder.get('ce_pe_compare') or {}
    if cmp_.get('note'):
        lines.append(f"\n_{cmp_['note']}_")
    return '\n'.join(lines)


def build_ladder_dashboard_payload() -> dict:
    try:
        from core.shared_state import STATE
        ladder = STATE.get('market.strike_ladder') or {}
        if ladder:
            return ladder
        price = STATE.get('market.price', 0)
        zone = STATE.get('zone', {}) or {}
        bias = zone.get('bias', 'BULLISH')
        expiry = zone.get('expiry', '')
        if price and expiry:
            return scan_strike_ladder(price, bias, expiry)
    except Exception:
        pass
    return {'ok': False, 'candidates': []}
