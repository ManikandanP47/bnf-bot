"""
Salary-trader guards — ₹5k capital, paper-first, option buying realities.

Addresses:
  • Theta + spread + late entries
  • One lot = large % of capital
  • Empty brain / cold start
  • Stale data (no trades on yfinance / historical-only)
  • Wednesday expiry chop
"""

import os
from datetime import datetime, time as dtime
import pytz

from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def check_data_quality(signal: dict, params: dict) -> dict:
    """Only trade on live Groww LTP — not delayed fallbacks."""
    if os.getenv('BLOCK_STALE_PRICE_TRADES', 'true').lower() != 'true':
        return {'ok': True, 'warnings': []}
    src = STATE.get('market.data_source', '')
    live_sources = ('GROWW', 'GROWW_FEED', 'GROWW_HIST')
    if src in live_sources:
        return {'ok': True, 'warnings': []}
    blocked = ('YFINANCE_FALLBACK', 'YFINANCE', 'DELTA_FALLBACK', 'DELTA_MODEL', 'UNAVAILABLE')
    if src in blocked or not src:
        return {
            'ok': False,
            'reason': (
                f'🛑 Price feed is *{src or "unknown"}* (not live Groww). '
                f'No entries on delayed data — protects against bad fills.'
            ),
        }
    return {'ok': True, 'warnings': [f'Feed source {src} — proceeding with caution']}


def check_cold_start_discipline(signal: dict, params: dict) -> dict:
    """Raise the bar while brain has few paper samples."""
    from src.brain_metrics import get_core_stats
    total = get_core_stats().get('total', 0)
    score = signal.get('score', 0)
    warnings = []

    if total < 5:
        need = _env_int('COLD_START_MIN_SCORE', 8)
        if score < need:
            return {
                'ok': False,
                'reason': (
                    f'🧠 Cold start ({total}/5 paper trades) — need score ≥ {need} '
                    f'(got {score}). Learning phase: extra strict.'
                ),
            }
        warnings.append(f'🧠 Cold start: only {total}/5 paper trades logged')
    elif total < 10:
        need = _env_int('COLD_START_MIN_TRADES_SCORE', 7)
        if score < need:
            return {
                'ok': False,
                'reason': (
                    f'🧠 Early brain ({total}/10 trades) — need score ≥ {need} '
                    f'(got {score}).'
                ),
            }

    return {'ok': True, 'warnings': warnings}


def check_theta_time_window(signal: dict, params: dict) -> dict:
    """Block late entries — theta eats option buyers after ~2 PM."""
    now = datetime.now(IST)
    t = now.time()
    session = signal.get('session', '')

    normal_cutoff = dtime(_env_int('NO_ENTRY_AFTER_HOUR', 14), 0)
    wed_cutoff = dtime(_env_int('NO_ENTRY_AFTER_HOUR_WED', 13), 0)
    cutoff = wed_cutoff if now.weekday() == 2 else normal_cutoff

    if t >= cutoff:
        return {
            'ok': False,
            'reason': (
                f'⏰ After {cutoff.strftime("%I %p")} IST — theta decay window. '
                f'Option buyers avoid late entries on ₹5k accounts.'
            ),
        }

    # Wednesday: no fresh afternoon leg at all
    if now.weekday() == 2 and session == 'AFTERNOON_MOVE':
        return {
            'ok': False,
            'reason': (
                '📅 Wednesday expiry — afternoon session blocked. '
                'Weekly expiry chop + theta too risky for small capital.'
            ),
        }

    warnings = []
    if session == 'AFTERNOON_MOVE':
        warnings.append('⚠️ Afternoon entry — prefer morning; theta accelerates after lunch')
    return {'ok': True, 'warnings': warnings}


def check_wednesday_expiry_day(signal: dict, params: dict) -> dict:
    """Extra strict on weekly expiry Wednesday (monthly handled by event calendar)."""
    if datetime.now(IST).weekday() != 2:
        return {'ok': True, 'warnings': []}
    score = signal.get('score', 0)
    need = _env_int('WEDNESDAY_MIN_SCORE', 8)
    if score < need:
        return {
            'ok': False,
            'reason': (
                f'📅 Wednesday expiry — need score ≥ {need} (got {score}). '
                f'Expiry-day gamma/theta not for ₹5k lottery trades.'
            ),
        }
    return {
        'ok': True,
        'warnings': ['⚠️ Wednesday expiry day — size and timing extra strict'],
    }


def check_capital_at_risk(signal: dict, params: dict) -> dict:
    """One trade must not risk more than X% of live capital at SL."""
    from src.capital_guard import LIVE_CAPITAL_RS, check_trade_cost_vs_capital

    lot_cost = params.get('lot_cost', 0) or 0
    if not lot_cost:
        prem = params.get('premium', 0) or STATE.get('zone', {}).get('premium', 0)
        lot_cost = prem * 15

    cost_chk = check_trade_cost_vs_capital(lot_cost)
    if cost_chk.get('blocked'):
        return {'ok': False, 'reason': cost_chk['reason']}

    max_loss = params.get('max_loss', 0)
    if not max_loss:
        prem = params.get('premium', 0) or 0
        max_loss = prem * 0.30 * 15

    cap_pct = _env_float('MAX_LOSS_PER_TRADE_PCT', 25)
    max_allowed = LIVE_CAPITAL_RS * cap_pct / 100
    if max_loss > max_allowed:
        return {
            'ok': False,
            'reason': (
                f'🛑 SL risk ₹{max_loss:,.0f} > {cap_pct:.0f}% of ₹{LIVE_CAPITAL_RS:,.0f} capital '
                f'(max ₹{max_allowed:,.0f}/trade). Pick cheaper strike or skip.'
            ),
        }

    warnings = []
    if lot_cost > LIVE_CAPITAL_RS * 0.65:
        warnings.append(
            f'💡 Lot uses {lot_cost / LIVE_CAPITAL_RS * 100:.0f}% of capital — one loss hurts'
        )
    return {'ok': True, 'warnings': warnings}


def check_expiry_theta(signal: dict, params: dict) -> dict:
    """Reject options expiring too soon (gamma/theta)."""
    from src.expiry_picker import days_to_expiry
    expiry = params.get('expiry', '') or STATE.get('zone', {}).get('expiry', '')
    min_days = _env_int('MIN_DAYS_TO_EXPIRY', 5)
    left = days_to_expiry(expiry)
    if expiry and left < min_days:
        return {
            'ok': False,
            'reason': (
                f'📉 Expiry in {left}d ({expiry}) — need ≥{min_days}d for ₹5k theta safety. '
                f'Evening scan should pick next-week expiry.'
            ),
        }
    return {'ok': True, 'warnings': []}


def run_salary_trader_guards(signal: dict, params: dict = None) -> dict:
    """Run all guards. Returns {ok, reason, warnings}."""
    params = params or {}
    warnings = []
    checks = (
        check_data_quality,
        check_cold_start_discipline,
        check_theta_time_window,
        check_wednesday_expiry_day,
        check_capital_at_risk,
        check_expiry_theta,
    )
    for fn in checks:
        r = fn(signal, params)
        if not r.get('ok', True):
            return r
        warnings.extend(r.get('warnings', []))
    return {'ok': True, 'reason': '', 'warnings': warnings}
