"""
IV-rank gates for option buyers — blocks expensive premium / IV crush setups.
Uses cached NSE chain in STATE (no extra API).
"""

import os

IV_RANK_BLOCK = float(os.getenv('IV_RANK_BLOCK', '80'))
IV_RANK_LOW = float(os.getenv('IV_RANK_LOW', '30'))
IV_RANK_OVERRIDE_SCORE = int(os.getenv('IV_RANK_OVERRIDE_SCORE', '9'))

MIN_DELTA_ABS = float(os.getenv('GREEKS_MIN_DELTA', '0.15'))
MAX_THETA_LOT_DAY = float(os.getenv('GREEKS_MAX_THETA_LOT_DAY', '130'))
MAX_THETA_LOT_AFTERNOON = float(os.getenv('GREEKS_MAX_THETA_AFTERNOON', '95'))


def _chain_iv_rank() -> float:
    try:
        from core.shared_state import STATE
        ch = STATE.get('market.option_chain') or {}
        v = ch.get('iv_rank')
        if v is not None:
            return float(v)
    except Exception:
        pass
    return 50.0


def iv_rank_score_delta() -> tuple:
    """Score adjustment for AnalysisAgent (+1 low IV, -1 high IV)."""
    rank = _chain_iv_rank()
    if rank >= IV_RANK_BLOCK:
        return -1, f"⚠️ IV rank {rank:.0f} — expensive premium (IV crush risk)"
    if rank <= IV_RANK_LOW:
        return 1, f"✅ IV rank {rank:.0f} — cheaper options for buyers"
    return 0, ''


def check_iv_rank_for_buyers(score: int) -> dict:
    """
    Risk gate: block new long-option entries when IV rank is very high
    unless setup score is exceptional.
    """
    rank = _chain_iv_rank()
    if rank < IV_RANK_BLOCK:
        return {'ok': True, 'iv_rank': rank}

    if score >= IV_RANK_OVERRIDE_SCORE:
        return {
            'ok': True,
            'iv_rank': rank,
            'warning': (
                f"⚠️ IV rank {rank:.0f} — only allowed because score "
                f"{score} ≥ {IV_RANK_OVERRIDE_SCORE}"
            ),
        }

    return {
        'ok': False,
        'iv_rank': rank,
        'reason': (
            f"📉 IV rank {rank:.0f} (≥{IV_RANK_BLOCK:.0f}) — "
            f"option buying into IV crush. Need score ≥{IV_RANK_OVERRIDE_SCORE} "
            f"(got {score})."
        ),
    }


def check_greeks_for_buyers(strike, opt_type: str, expiry: str,
                            premium: float = 0, session: str = '') -> dict:
    """
    Per-contract Greeks gate for option buyers.
    Blocks lottery-ticket delta and excessive theta decay.
    """
    if not strike or not opt_type or not expiry:
        return {'ok': True, 'reason': 'greeks skipped (no contract)'}

    try:
        from src.option_greeks import greeks_for_contract
        g = greeks_for_contract(float(strike), opt_type, expiry, premium)
    except Exception as e:
        return {'ok': True, 'reason': f'greeks skipped ({str(e)[:30]})'}

    delta = abs(float(g.get('delta', 0) or 0))
    theta_lot = abs(float(g.get('theta_per_lot_day', 0) or 0))

    if delta > 0 and delta < MIN_DELTA_ABS:
        return {
            'ok': False,
            'reason': (
                f"📉 Delta {delta:.2f} < {MIN_DELTA_ABS} — lottery OTM, "
                f"pro traders skip"
            ),
            'delta': delta,
            'theta_lot': theta_lot,
        }

    theta_cap = MAX_THETA_LOT_AFTERNOON if session in (
        'AFTERNOON_MOVE', 'EOD_CHOP', 'LUNCH_CHOP',
    ) else MAX_THETA_LOT_DAY
    if theta_lot > theta_cap:
        return {
            'ok': False,
            'reason': (
                f"⏳ Theta ₹{theta_lot:.0f}/lot/day > ₹{theta_cap:.0f} cap "
                f"({session or 'session'}) — theta eats small accounts"
            ),
            'delta': delta,
            'theta_lot': theta_lot,
        }

    return {
        'ok': True,
        'reason': f"✅ δ{delta:.2f} θ₹{theta_lot:.0f}/lot/d",
        'delta': delta,
        'theta_lot': theta_lot,
    }
