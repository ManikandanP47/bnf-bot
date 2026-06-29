"""Max pain pin risk — block option buys when spot is pinned away from edge."""

import os

MAX_PAIN_DIST_PCT = float(os.getenv('MAX_PAIN_DIST_PCT', '0.4'))


def check_max_pain_pin(bias: str, price: float) -> dict:
    """
    CE: block if spot too far above max pain (pin down risk).
    PE: block if spot too far below max pain.
    """
    if price <= 0:
        return {'ok': True, 'reason': ''}

    try:
        from core.shared_state import STATE
        mp = STATE.get('market.oi_deep') or {}
        if not mp.get('available'):
            from src.oi_analysis import get_oi_data, calculate_max_pain
            from src.api_scheduler import should_fetch, mark_fetched
            if not should_fetch('nse_oi'):
                mp = STATE.get('market.oi_deep') or {}
            else:
                raw = get_oi_data()
                mark_fetched('nse_oi')
                if raw:
                    mp = calculate_max_pain(raw)
                    STATE.set('market.oi_deep', mp)

        if not mp.get('available'):
            return {'ok': True, 'reason': 'Max pain check skipped'}

        max_pain = mp.get('max_pain', 0)
        if max_pain <= 0:
            return {'ok': True, 'reason': ''}

        dist_pct = (price - max_pain) / price * 100

        if bias == 'BULLISH' and dist_pct > MAX_PAIN_DIST_PCT:
            return {
                'ok': False,
                'reason': (
                    f'📌 Spot {dist_pct:+.2f}% above max pain {max_pain:,.0f} '
                    f'— pin risk for CE'
                ),
                'max_pain': max_pain,
                'dist_pct': dist_pct,
            }
        if bias == 'BEARISH' and dist_pct < -MAX_PAIN_DIST_PCT:
            return {
                'ok': False,
                'reason': (
                    f'📌 Spot {dist_pct:+.2f}% below max pain {max_pain:,.0f} '
                    f'— pin risk for PE'
                ),
                'max_pain': max_pain,
                'dist_pct': dist_pct,
            }

        return {
            'ok': True,
            'reason': f'✅ Max pain {max_pain:,.0f} ({dist_pct:+.2f}% from spot)',
            'max_pain': max_pain,
        }
    except Exception as e:
        return {'ok': True, 'reason': f'Max pain skipped ({str(e)[:20]})'}
