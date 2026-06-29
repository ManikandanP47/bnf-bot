"""
Chart levels — auto support/resistance like traders draw on charts.
Uses swing highs/lows from live 5m/15m candles (no manual lines needed).
"""

from typing import Optional


def _swing_points(candles: list, wing: int = 2) -> tuple[list, list]:
    """Local swing highs and lows."""
    highs, lows = [], []
    if len(candles) < wing * 2 + 1:
        return highs, lows
    for i in range(wing, len(candles) - wing):
        h = candles[i]['high']
        l = candles[i]['low']
        if all(h >= candles[i - j]['high'] for j in range(1, wing + 1)) and \
           all(h >= candles[i + j]['high'] for j in range(1, wing + 1)):
            highs.append({'price': h, 'time': candles[i].get('time', ''), 'idx': i})
        if all(l <= candles[i - j]['low'] for j in range(1, wing + 1)) and \
           all(l <= candles[i + j]['low'] for j in range(1, wing + 1)):
            lows.append({'price': l, 'time': candles[i].get('time', ''), 'idx': i})
    return highs, lows


def _nearest_above(levels: list, price: float) -> Optional[float]:
    above = [x['price'] for x in levels if x['price'] > price]
    return min(above) if above else None


def _nearest_below(levels: list, price: float) -> Optional[float]:
    below = [x['price'] for x in levels if x['price'] < price]
    return max(below) if below else None


def compute_chart_levels(candles_5m: list, candles_15m: list, price: float) -> dict:
    """
  Returns nearest S/R, trend bias from swing structure.
  Mimics: horizontal lines at swing points + short-term trend.
    """
    if not price or len(candles_15m) < 8:
        return {'available': False}

    h15, l15 = _swing_points(candles_15m[-40:], wing=2)
    h5, l5 = _swing_points(candles_5m[-30:], wing=2)

    res_15 = _nearest_above(h15, price)
    sup_15 = _nearest_below(l15, price)
    res_5  = _nearest_above(h5, price)
    sup_5  = _nearest_below(l5, price)

    trend = 'NEUTRAL'
    trend_note = ''
    if len(l15) >= 2:
        if l15[-1]['price'] > l15[-2]['price']:
            trend = 'BULLISH'
            trend_note = f"Higher lows: {l15[-2]['price']:,.0f} → {l15[-1]['price']:,.0f}"
        elif l15[-1]['price'] < l15[-2]['price']:
            trend = 'BEARISH'
            trend_note = f"Lower highs structure on swings"

    if len(h15) >= 2 and h15[-1]['price'] < h15[-2]['price']:
        if trend == 'BULLISH':
            trend = 'NEUTRAL'
        trend_note = (trend_note + ' | ' if trend_note else '') + \
            f"Lower high {h15[-2]['price']:,.0f} → {h15[-1]['price']:,.0f}"

    return {
        'available':    True,
        'resistance_15m': res_15,
        'support_15m':    sup_15,
        'resistance_5m':  res_5,
        'support_5m':     sup_5,
        'swing_trend':    trend,
        'trend_note':     trend_note,
        'swing_highs':    len(h15),
        'swing_lows':     len(l15),
    }


def check_chart_levels(price: float, bias: str, levels: dict) -> dict:
    """Block chasing into nearest resistance/support."""
    if not levels.get('available'):
        return {'ok': True, 'score_delta': 0, 'warnings': []}

    warnings = []
    delta = 0
    res = levels.get('resistance_15m') or levels.get('resistance_5m')
    sup = levels.get('support_15m') or levels.get('support_5m')

    if bias == 'BULLISH' and res and price > 0:
        dist_pct = (res - price) / price * 100
        if dist_pct < 0.08:
            return {
                'ok': False,
                'reason': (
                    f'📏 Chart resistance {res:,.0f} only {dist_pct:.2f}% above — '
                    f'CE into ceiling, wait for break'
                ),
            }
        if dist_pct < 0.25:
            warnings.append(f'⚠️ Near 15m resistance {res:,.0f}')

    if bias == 'BEARISH' and sup and price > 0:
        dist_pct = (price - sup) / price * 100
        if dist_pct < 0.08:
            return {
                'ok': False,
                'reason': (
                    f'📏 Chart support {sup:,.0f} only {dist_pct:.2f}% below — '
                    f'PE into floor, wait for break'
                ),
            }

    st = levels.get('swing_trend', '')
    if st == bias:
        delta += 1
        warnings.append(f'✅ Swing structure {st} aligns with {bias}')

    return {'ok': True, 'score_delta': delta, 'warnings': warnings}
