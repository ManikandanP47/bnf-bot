"""ADX trend strength + learning-phase regime gate."""

import os

ADX_MIN = float(os.getenv('ADX_MIN', '18'))
BLOCK_RANGING_LEARNING = os.getenv('BLOCK_RANGING_LEARNING', 'true').lower() == 'true'


def calc_adx(candles: list, period: int = 14) -> float:
    """ADX from OHLC candle list (needs 2*period+ candles)."""
    if len(candles) < period + 2:
        return 0.0
    try:
        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(candles)):
            h, l, c0 = candles[i]['high'], candles[i]['low'], candles[i - 1]['close']
            c1 = candles[i]['close']
            tr = max(h - l, abs(h - c0), abs(l - c0))
            up = h - candles[i - 1]['high']
            down = candles[i - 1]['low'] - l
            tr_list.append(tr)
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)

        def _smooth(vals):
            s = sum(vals[:period])
            out = [s]
            for v in vals[period:]:
                s = s - s / period + v
                out.append(s)
            return out

        tr_s = _smooth(tr_list)
        p_s = _smooth(plus_dm)
        m_s = _smooth(minus_dm)
        if not tr_s or tr_s[-1] == 0:
            return 0.0
        di_p = 100 * (p_s[-1] / tr_s[-1])
        di_m = 100 * (m_s[-1] / tr_s[-1])
        denom = di_p + di_m
        if denom == 0:
            return 0.0
        dx = abs(di_p - di_m) / denom * 100
        return round(dx, 1)
    except Exception:
        return 0.0


def check_trend_strength(candles_15m: list, regime: str, bias: str) -> dict:
    """Block chop / ranging during learning phase."""
    adx = calc_adx(candles_15m)
    try:
        from src.shadow_learning import is_learning_phase
        learning = is_learning_phase()
    except Exception:
        learning = True

    if learning and BLOCK_RANGING_LEARNING:
        if regime in ('RANGING', 'TIGHT_RANGE'):
            return {
                'ok': False,
                'adx': adx,
                'reason': f'📊 {regime} market — learning phase skips chop',
            }
        if adx > 0 and adx < ADX_MIN:
            return {
                'ok': False,
                'adx': adx,
                'reason': f'📊 ADX {adx:.0f} < {ADX_MIN:.0f} — no trend, skip',
            }

    if adx >= ADX_MIN:
        return {'ok': True, 'adx': adx, 'reason': f'✅ ADX {adx:.0f} — trending', 'score_delta': 1}
    return {'ok': True, 'adx': adx, 'reason': f'ADX {adx:.0f}', 'score_delta': 0}
