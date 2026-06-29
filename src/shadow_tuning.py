"""
Shadow WR auto-tuning — raise min score when virtual drills underperform.
"""

import os

SHADOW_WR_FLOOR = float(os.getenv('SHADOW_WR_FLOOR', os.getenv('SHADOW_MIN_WR', '40')))
SHADOW_TUNE_DAYS = int(os.getenv('SHADOW_TUNE_DAYS', '14'))
SHADOW_MIN_SAMPLES = int(os.getenv('SHADOW_MIN_SAMPLES', '10'))


def shadow_score_adjustment(base_min: int = 5) -> dict:
    """
    If shadow win rate < 40% over 14d with enough samples,
    tighten min score by +1 or +2.
    """
    try:
        from src.learning_scoreboard import shadow_vs_paper_stats
        s = shadow_vs_paper_stats(SHADOW_TUNE_DAYS)
        n = s.get('shadow_n', 0)
        wr = s.get('shadow_wr', 0)
        if n < SHADOW_MIN_SAMPLES:
            return {
                'boost': 0,
                'min_score': base_min,
                'reason': '',
                'shadow_n': n,
                'shadow_wr': wr,
            }

        boost = 0
        reason = ''
        if wr < SHADOW_WR_FLOOR - 10:
            boost = 2
            reason = (
                f'🎓 Shadow WR {wr}% over {SHADOW_TUNE_DAYS}d ({n} drills) — '
                f'min score +2 until edge improves'
            )
        elif wr < SHADOW_WR_FLOOR:
            boost = 1
            reason = (
                f'🎓 Shadow WR {wr}% over {SHADOW_TUNE_DAYS}d — min score +1 (tighten entries)'
            )
        elif wr >= 55 and n >= 15:
            reason = f'✅ Shadow WR {wr}% — virtual edge OK'

        return {
            'boost': boost,
            'min_score': base_min + boost,
            'reason': reason,
            'shadow_n': n,
            'shadow_wr': wr,
        }
    except Exception:
        return {'boost': 0, 'min_score': base_min, 'reason': '', 'shadow_n': 0, 'shadow_wr': 0}
