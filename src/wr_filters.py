"""
Win-rate filters — flow score, VWAP, OI walls, expiry week,
zone confirmation, premium sweet spot, shadow agreement.
"""

import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')

MIN_FLOW_SCORE = int(os.getenv('MIN_FLOW_SCORE', '4'))
FLOW_SCORE_BYPASS_AT = int(os.getenv('FLOW_SCORE_BYPASS_AT', '8'))
ZONE_CONFIRM_MINUTES = int(os.getenv('ZONE_CONFIRM_MINUTES', '10'))
SWEET_PREMIUM_MIN = float(os.getenv('SWEET_PREMIUM_MIN', '120'))
SWEET_PREMIUM_MAX = float(os.getenv('SWEET_PREMIUM_MAX', '280'))
EXPIRY_WEEK_SHADOW_WR = float(os.getenv('EXPIRY_WEEK_SHADOW_WR', '50'))
EXPIRY_WEEK_SHADOW_MIN = int(os.getenv('EXPIRY_WEEK_SHADOW_MIN', '5'))
SHADOW_LOSS_BLOCK_COUNT = int(os.getenv('SHADOW_LOSS_BLOCK_COUNT', '2'))
SESSION_WR_FLOOR = float(os.getenv('SESSION_WR_FLOOR', '35'))
SESSION_MIN_SAMPLES = int(os.getenv('SESSION_MIN_SAMPLES', '5'))
OI_WALL_DISTANCE_PCT = float(os.getenv('OI_WALL_DISTANCE_PCT', '0.3'))


def check_vwap_hard(price: float, vwap: float, bias: str) -> dict:
    """CE only above VWAP; PE only below — hard block."""
    if vwap <= 0 or price <= 0:
        return {'ok': True, 'reason': ''}
    if bias == 'BULLISH' and price < vwap * 0.999:
        return {
            'ok': False,
            'reason': (
                f'📉 Price {price:,.0f} below VWAP {vwap:,.0f} — '
                f'CE needs VWAP reclaim, skip'
            ),
        }
    if bias == 'BEARISH' and price > vwap * 1.001:
        return {
            'ok': False,
            'reason': (
                f'📈 Price {price:,.0f} above VWAP {vwap:,.0f} — '
                f'PE needs price below VWAP, skip'
            ),
        }
    return {'ok': True, 'reason': f'✅ VWAP aligned ({vwap:,.0f})'}


def check_zone_confirmation(in_zone: bool) -> dict:
    """Wait N minutes after first zone touch before entry."""
    from core.shared_state import STATE

    now = datetime.now(IST)
    key = 'zone.touch_started_at'

    if not in_zone:
        STATE.set(key, '')
        return {'ok': True, 'reason': ''}

    started = STATE.get(key, '')
    if not started:
        STATE.set(key, now.isoformat())
        return {
            'ok': False,
            'reason': (
                f'⏳ Zone touch — waiting {ZONE_CONFIRM_MINUTES}m '
                f'for confirmation candles'
            ),
        }

    try:
        t0 = datetime.fromisoformat(started)
        if t0.tzinfo is None:
            t0 = IST.localize(t0)
        mins = (now - t0).total_seconds() / 60
        if mins < ZONE_CONFIRM_MINUTES:
            left = int(ZONE_CONFIRM_MINUTES - mins) + 1
            return {
                'ok': False,
                'reason': (
                    f'⏳ Zone confirmation — {left}m left '
                    f'(avoid first-touch fakeouts)'
                ),
            }
    except Exception:
        STATE.set(key, now.isoformat())
        return {'ok': False, 'reason': '⏳ Zone confirmation timer started'}

    return {'ok': True, 'reason': '✅ Zone confirmed after wait'}


def check_min_flow_score(flow: dict, signal_score: int) -> dict:
    """Require minimum F&O flow score unless setup score is very high."""
    fs = flow.get('flow_score', 0)
    if signal_score >= FLOW_SCORE_BYPASS_AT:
        return {
            'ok': True,
            'reason': f'Flow {fs}/6 — bypass (score {signal_score} ≥ {FLOW_SCORE_BYPASS_AT})',
        }
    if fs < MIN_FLOW_SCORE:
        return {
            'ok': False,
            'reason': (
                f'🌊 Flow score {fs}/6 < min {MIN_FLOW_SCORE} — '
                f'F&O context weak, skip'
            ),
        }
    return {'ok': True, 'reason': f'✅ Flow score {fs}/6'}


def check_oi_wall_veto(bias: str, price: float, target_price: float = 0) -> dict:
    """
    Hard veto when index target sits at CE/PE OI wall with fresh writing.
    """
    if price <= 0:
        return {'ok': True, 'reason': ''}

    try:
        from src.oi_analysis import get_oi_data, calculate_max_pain
        from src.oi_change import analyse_oi_change

        raw = get_oi_data()
        if not raw:
            return {'ok': True, 'reason': 'OI wall check skipped (no data)'}

        mp = calculate_max_pain(raw)
        if not mp.get('available'):
            return {'ok': True, 'reason': ''}

        resistance = mp.get('resistance', 0)
        support = mp.get('support', 0)
        oi_chg = analyse_oi_change(bias, price)
        ce_chg = oi_chg.get('ce_chg_at_resistance', 0) if oi_chg.get('available') else 0
        pe_chg = oi_chg.get('pe_chg_at_support', 0) if oi_chg.get('available') else 0

        tgt = target_price or (resistance if bias == 'BULLISH' else support)

        if bias == 'BULLISH' and resistance > 0:
            dist_pct = abs(price - resistance) / price * 100
            tgt_through = tgt > resistance
            tgt_near = tgt > 0 and abs(tgt - resistance) / price * 100 < OI_WALL_DISTANCE_PCT
            fresh_write = ce_chg > 30000 or oi_chg.get('block')

            if (dist_pct < OI_WALL_DISTANCE_PCT or tgt_near or tgt_through) and fresh_write:
                return {
                    'ok': False,
                    'reason': (
                        f'🧱 CE OI wall at {resistance:,.0f} '
                        f'(Δ OI {ce_chg:+,}) — target blocked'
                    ),
                }

        if bias == 'BEARISH' and support > 0:
            dist_pct = abs(price - support) / price * 100
            tgt_through = tgt < support if tgt > 0 else False
            tgt_near = tgt > 0 and abs(tgt - support) / price * 100 < OI_WALL_DISTANCE_PCT
            fresh_write = pe_chg > 30000 or oi_chg.get('block')

            if (dist_pct < OI_WALL_DISTANCE_PCT or tgt_near or tgt_through) and fresh_write:
                return {
                    'ok': False,
                    'reason': (
                        f'🧱 PE OI wall at {support:,.0f} '
                        f'(Δ OI {pe_chg:+,}) — target blocked'
                    ),
                }

        return {'ok': True, 'reason': '✅ OI wall clear'}
    except Exception as e:
        return {'ok': True, 'reason': f'OI wall check skipped ({str(e)[:25]})'}


def check_expiry_week_rules(signal_score: int) -> dict:
    """Tighter rules Mon–Wed of expiry week."""
    from src.expiry_picker import is_expiry_week

    if not is_expiry_week():
        return {'ok': True, 'min_score_boost': 0, 'reason': ''}

    boost = 1
    weekday = datetime.now(IST).weekday()

    if weekday <= 2:
        try:
            from src.learning_scoreboard import shadow_vs_paper_stats
            s = shadow_vs_paper_stats(7)
            n = s.get('shadow_n', 0)
            wr = s.get('shadow_wr', 0)
            if n >= EXPIRY_WEEK_SHADOW_MIN and wr < EXPIRY_WEEK_SHADOW_WR:
                return {
                    'ok': False,
                    'min_score_boost': boost,
                    'reason': (
                        f'📅 Expiry week Mon–Wed — shadow WR {wr}% < '
                        f'{EXPIRY_WEEK_SHADOW_WR}% — skip new entries'
                    ),
                }
        except Exception:
            pass

        if signal_score < 7 + boost:
            return {
                'ok': False,
                'min_score_boost': boost,
                'reason': (
                    f'📅 Expiry week — need score ≥ {7 + boost} '
                    f'(theta crush risk)'
                ),
            }

    return {
        'ok': True,
        'min_score_boost': boost,
        'reason': f'📅 Expiry week — min score +{boost}',
    }


def check_premium_sweet_spot(premium: float) -> dict:
    """Prefer ₹120–₹280 premium band for ₹5k accounts."""
    if premium <= 0:
        return {'ok': True, 'reason': ''}
    if premium < SWEET_PREMIUM_MIN:
        return {
            'ok': False,
            'reason': (
                f'💸 Premium ₹{premium:.0f} below sweet spot '
                f'(₹{SWEET_PREMIUM_MIN:.0f}–₹{SWEET_PREMIUM_MAX:.0f}) — thin edge'
            ),
        }
    if premium > SWEET_PREMIUM_MAX:
        return {
            'ok': False,
            'reason': (
                f'💸 Premium ₹{premium:.0f} above sweet spot '
                f'(₹{SWEET_PREMIUM_MIN:.0f}–₹{SWEET_PREMIUM_MAX:.0f}) — theta/capital risk'
            ),
        }
    return {'ok': True, 'reason': f'✅ Premium ₹{premium:.0f} in sweet spot'}


def check_shadow_agreement(bias: str) -> dict:
    """Block if today's shadow drills on same bias lost 2+ times."""
    try:
        from src.shadow_learning import get_today_shadow_trades
        trades = get_today_shadow_trades()
        same = [t for t in trades if t.get('bias') == bias and t.get('outcome')]
        losses = sum(1 for t in same if t.get('outcome') == 'LOSS')
        if losses >= SHADOW_LOSS_BLOCK_COUNT:
            return {
                'ok': False,
                'reason': (
                    f'🎓 Shadow lost {losses}× on {bias} today — '
                    f'virtual edge says skip'
                ),
            }
        wins = sum(1 for t in same if t.get('outcome') == 'WIN')
        if wins >= 2:
            return {'ok': True, 'reason': f'✅ Shadow {wins}W on {bias} today'}
    except Exception:
        pass
    return {'ok': True, 'reason': ''}


def check_session_win_rate(session: str) -> dict:
    """Block sessions with historically poor win rate."""
    if not session:
        return {'ok': True, 'reason': ''}
    try:
        from src.trade_analytics import session_expectancy
        exp = session_expectancy()
        s = exp.get(session)
        if not s or s['trades'] < SESSION_MIN_SAMPLES:
            return {'ok': True, 'reason': ''}
        if s['win_rate'] < SESSION_WR_FLOOR:
            return {
                'ok': False,
                'reason': (
                    f'📊 {session} WR {s["win_rate"]}% < {SESSION_WR_FLOOR}% '
                    f'({s["trades"]} trades) — skip session'
                ),
            }
    except Exception:
        pass
    return {'ok': True, 'reason': ''}
