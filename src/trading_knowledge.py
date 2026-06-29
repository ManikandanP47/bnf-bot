"""
Trading knowledge engine — candles, levels, theta, history alignment.
Encodes rules a discretionary trader would apply before clicking buy.
"""

from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def _max_theta_risk() -> int:
    import os
    try:
        return int(os.getenv('MAX_THETA_RISK_SCORE', '55'))
    except ValueError:
        return 55


def _near(level: float, price: float, pct: float = 0.12) -> bool:
    if not level or not price:
        return False
    return abs(price - level) / price * 100 <= pct


def detect_5m_patterns(candles: list, bias: str) -> dict:
    """Last closed 5m candle patterns."""
    if len(candles) < 3:
        return {'patterns': [], 'score_delta': 0, 'block': False, 'reason': ''}

    c = candles[-2]  # last closed (not forming)
    p = candles[-3]
    body = abs(c['close'] - c['open'])
    rng  = max(c['high'] - c['low'], 1)
    upper_wick = c['high'] - max(c['open'], c['close'])
    lower_wick = min(c['open'], c['close']) - c['low']
    patterns = []
    delta = 0

    # Bullish engulfing
    if (bias == 'BULLISH' and c['close'] > c['open'] and p['close'] < p['open']
            and c['close'] > p['open'] and c['open'] < p['close']):
        patterns.append('bullish_engulfing')
        delta += 1

    # Bearish engulfing
    if (bias == 'BEARISH' and c['close'] < c['open'] and p['close'] > p['open']
            and c['close'] < p['open'] and c['open'] > p['close']):
        patterns.append('bearish_engulfing')
        delta += 1

    # Pin bar / rejection
    if lower_wick > body * 2 and lower_wick > upper_wick * 2 and bias == 'BULLISH':
        patterns.append('bullish_pin')
        delta += 1
    if upper_wick > body * 2 and upper_wick > lower_wick * 2 and bias == 'BEARISH':
        patterns.append('bearish_pin')
        delta += 1

    # Doji indecision at extension — caution
    if body / rng < 0.15:
        patterns.append('doji')
        delta -= 1

    return {'patterns': patterns, 'score_delta': delta, 'block': False, 'reason': ''}


def check_level_alignment(price: float, bias: str, ctx: dict) -> dict:
    """Don't buy CE into PDH wall or PE into PDL floor without breakout."""
    if not ctx.get('available') or not price:
        return {'ok': True, 'score_delta': 0, 'reason': '', 'warnings': []}

    pdh, pdl = ctx.get('pdh', 0), ctx.get('pdl', 0)
    pwh, pwl = ctx.get('pwh', 0), ctx.get('pwl', 0)
    warnings = []
    delta = 0

    if bias == 'BULLISH':
        if _near(pdh, price) and price < pdh:
            return {
                'ok': False,
                'score_delta': 0,
                'reason': (
                    f'🧱 CE into PDH wall ({pdh:,.0f}) — wait for clean breakout above'
                ),
            }
        if price > pdh:
            delta += 1
            warnings.append(f'✅ Above PDH {pdh:,.0f} — breakout context')
        if _near(pwl, price):
            delta += 1
            warnings.append(f'✅ Near PWL support {pwl:,.0f}')

    if bias == 'BEARISH':
        if _near(pdl, price) and price > pdl:
            return {
                'ok': False,
                'score_delta': 0,
                'reason': (
                    f'🧱 PE into PDL floor ({pdl:,.0f}) — wait for breakdown below'
                ),
            }
        if price < pdl:
            delta += 1
            warnings.append(f'✅ Below PDL {pdl:,.0f} — breakdown context')
        if _near(pwh, price):
            delta += 1
            warnings.append(f'✅ Near PWH resistance {pwh:,.0f}')

    return {'ok': True, 'score_delta': delta, 'reason': '', 'warnings': warnings}


def check_theta_context(ctx: dict) -> dict:
    th = ctx.get('theta', {})
    score = th.get('score', 0)
    if score > _max_theta_risk():
        return {
            'ok': False,
            'score_delta': 0,
            'reason': (
                f'⏳ Theta risk {score}/100 ({th.get("level")}) — '
                f'time + expiry unfavourable for option buying'
            ),
        }
    if score >= 40:
        return {
            'ok': True,
            'score_delta': -1,
            'reason': '',
            'warnings': [f'⚠️ Theta risk {score}/100 — size down mentally'],
        }
    return {'ok': True, 'score_delta': 0, 'reason': '', 'warnings': []}


def check_cpr_alignment(price: float, bias: str, ctx: dict) -> dict:
    """CPR rules used by Indian discretionary traders."""
    cpr = ctx.get('cpr') or {}
    pos = ctx.get('cpr_position') or {}
    if not cpr.get('available') or not price:
        return {'ok': True, 'score_delta': 0, 'reason': '', 'warnings': []}

    warnings = []
    delta = 0
    wc = cpr.get('width_class', '')
    zone = pos.get('zone', '')
    virgin = pos.get('virgin')

    if wc == 'WIDE' and zone in ('UPPER_CPR', 'LOWER_CPR'):
        return {
            'ok': False,
            'score_delta': 0,
            'reason': (
                f'📐 Wide CPR + price inside range — chop day. '
                f'Wait for clean break of TC {cpr["tc"]:,.0f} or BC {cpr["bc"]:,.0f}'
            ),
        }

    if bias == 'BULLISH':
        if virgin == 'VIRGIN_BULL':
            delta += 2
            warnings.append('✅ Virgin CPR bull — trend day bias')
        elif zone == 'ABOVE_CPR':
            delta += 1
            warnings.append(f'✅ Above CPR TC {cpr["tc"]:,.0f}')
        elif zone == 'BELOW_CPR':
            return {
                'ok': False,
                'score_delta': 0,
                'reason': f'📐 CE below CPR BC {cpr["bc"]:,.0f} — wrong side of pivot',
            }
    if bias == 'BEARISH':
        if virgin == 'VIRGIN_BEAR':
            delta += 2
            warnings.append('✅ Virgin CPR bear — trend day bias')
        elif zone == 'BELOW_CPR':
            delta += 1
            warnings.append(f'✅ Below CPR BC {cpr["bc"]:,.0f}')
        elif zone == 'ABOVE_CPR':
            return {
                'ok': False,
                'score_delta': 0,
                'reason': f'📐 PE above CPR TC {cpr["tc"]:,.0f} — wrong side of pivot',
            }

    if wc == 'NARROW':
        warnings.append(f'📐 Narrow CPR ({cpr["width_pct"]:.2f}%) — breakout potential')

    return {'ok': True, 'score_delta': delta, 'reason': '', 'warnings': warnings}


def check_backtest_alignment(ctx: dict) -> dict:
    """Use last quick-backtest stats to tighten on weak historical edge."""
    from core.shared_state import STATE
    bt = STATE.get('system.backtest_summary') or {}
    if not bt.get('available'):
        return {'ok': True, 'score_delta': 0, 'reason': '', 'warnings': []}

    wr = bt.get('proxy_win_rate', 50)
    warnings = []
    delta = 0
    if bt.get('days_tested', 0) >= 5 and wr < 40:
        return {
            'ok': False,
            'score_delta': 0,
            'reason': (
                f'📉 History backtest proxy WR {wr:.0f}% over {bt["days_tested"]}d — '
                f'setup quality weak, skip until market improves'
            ),
        }
    if wr >= 55:
        delta += 1
        warnings.append(f'✅ History proxy WR {wr:.0f}% supports edge')
    return {'ok': True, 'score_delta': delta, 'reason': '', 'warnings': warnings}


def run_knowledge_checks(signal: dict, candles_5m: list = None) -> dict:
    """
    Full knowledge pass. Returns {ok, reason, score_delta, patterns, warnings}.
    """
    from core.shared_state import STATE
    from src.market_context import build_market_context

    ctx = STATE.get('market.context') or build_market_context()
    price = signal.get('price', 0)
    bias  = signal.get('trend', 'BULLISH')
    c5m   = candles_5m or STATE.get('market.candles_5m', [])

    warnings = []
    score_delta = 0
    patterns = []

    for fn in (check_theta_context, check_backtest_alignment):
        r = fn(ctx)
        if not r.get('ok', True):
            return {
                'ok': False,
                'reason': r['reason'],
                'score_delta': 0,
                'patterns': patterns,
                'warnings': warnings,
            }
        score_delta += r.get('score_delta', 0)
        warnings.extend(r.get('warnings', []))

    cpr_r = check_cpr_alignment(price, bias, ctx)
    if not cpr_r.get('ok', True):
        return {
            'ok': False,
            'reason': cpr_r['reason'],
            'score_delta': 0,
            'patterns': patterns,
            'warnings': warnings,
        }
    score_delta += cpr_r.get('score_delta', 0)
    warnings.extend(cpr_r.get('warnings', []))

    lvl = check_level_alignment(price, bias, ctx)
    if not lvl.get('ok', True):
        return {
            'ok': False,
            'reason': lvl['reason'],
            'score_delta': 0,
            'patterns': patterns,
            'warnings': warnings,
        }
    score_delta += lvl.get('score_delta', 0)
    warnings.extend(lvl.get('warnings', []))

    pat = detect_5m_patterns(c5m, bias)
    patterns = pat.get('patterns', [])
    score_delta += pat.get('score_delta', 0)

    from src.market_rag import apply_rag_to_signal
    rag = apply_rag_to_signal(signal)
    if not rag.get('ok', True):
        return {
            'ok': False,
            'reason': rag['reason'],
            'score_delta': 0,
            'patterns': patterns,
            'warnings': warnings,
        }
    score_delta += rag.get('score_delta', 0)
    for line in rag.get('reasons', [])[:2]:
        warnings.append(line)

    return {
        'ok': True,
        'reason': '',
        'score_delta': score_delta,
        'patterns': patterns,
        'warnings': warnings,
    }
