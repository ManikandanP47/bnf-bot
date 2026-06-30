"""
IV-rank gates for option buyers — blocks expensive premium / IV crush setups.
Uses cached NSE chain in STATE (no extra API).
"""

import os

IV_RANK_BLOCK = float(os.getenv('IV_RANK_BLOCK', '80'))
IV_RANK_LOW = float(os.getenv('IV_RANK_LOW', '30'))
IV_RANK_OVERRIDE_SCORE = int(os.getenv('IV_RANK_OVERRIDE_SCORE', '9'))


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
