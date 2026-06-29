"""
Market context — PDH/PDL, week levels, pivots, theta risk.
Uses Groww historical (primary) with yfinance fallback for prior-day bars.
"""

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')


def _daily_bars_from_candles(candles: list) -> list:
    """Aggregate intraday candles into per-day OHLC."""
    by_day = {}
    for c in candles:
        d = c['ts'].date() if hasattr(c.get('ts'), 'date') else None
        if not d:
            continue
        if d not in by_day:
            by_day[d] = {
                'date': d, 'open': c['open'], 'high': c['high'],
                'low': c['low'], 'close': c['close'],
            }
        else:
            b = by_day[d]
            b['high']  = max(b['high'], c['high'])
            b['low']   = min(b['low'], c['low'])
            b['close'] = c['close']
    return [by_day[k] for k in sorted(by_day.keys())]


def _fetch_groww_intraday(token: str, lookback_days: int) -> list:
    from src.groww_historical import fetch_banknifty_candles
    hours = min(24 * lookback_days, 240)
    return fetch_banknifty_candles(interval_min=15, lookback_hours=hours, token=token)


def _fetch_yfinance_daily(lookback_days: int) -> list:
    try:
        import yfinance as yf
        hist = yf.Ticker('^NSEBANK').history(
            period=f'{lookback_days + 5}d', interval='1d'
        ).dropna()
        out = []
        for idx, row in hist.iterrows():
            d = idx.date() if hasattr(idx, 'date') else idx
            out.append({
                'date': d,
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'close': float(row['Close']),
            })
        return out
    except Exception:
        return []


def compute_theta_risk(dte_days: int, hour: int, weekday: int) -> dict:
    """
    Theta/gamma risk 0–100 for option buyers (higher = worse).
    ₹5k accounts should avoid entries when risk > threshold.
    """
    score = 0
    notes = []
    if dte_days <= 2:
        score += 45
        notes.append('expiry ≤2d')
    elif dte_days <= 5:
        score += 25
        notes.append('expiry ≤5d')
    if hour >= 14:
        score += 35
        notes.append('after 2 PM')
    elif hour >= 12:
        score += 20
        notes.append('after noon')
    if weekday == 2:
        score += 25
        notes.append('Wednesday expiry')
    score = min(100, score)
    level = 'LOW' if score < 35 else 'MEDIUM' if score < 60 else 'HIGH'
    return {'score': score, 'level': level, 'notes': notes}


def build_market_context(token: str = '') -> dict:
    """PDH/PDL, week high/low, pivots, theta — for knowledge engine."""
    today = datetime.now(IST).date()
    bars = _daily_bars_from_candles(_fetch_groww_intraday(token, 12))
    source = 'GROWW'
    if len(bars) < 2:
        bars = _fetch_yfinance_daily(10)
        source = 'YFINANCE' if bars else 'NONE'

    ctx = {
        'available': False,
        'source':    source,
        'updated':   datetime.now(IST).strftime('%H:%M'),
    }
    if len(bars) < 2:
        return ctx

    prev_days = [b for b in bars if b['date'] < today]
    if not prev_days:
        prev_days = bars[:-1]
    if not prev_days:
        return ctx

    prev = prev_days[-1]
    week = prev_days[-5:] if len(prev_days) >= 5 else prev_days

    pdh = round(prev['high'], 2)
    pdl = round(prev['low'], 2)
    pdc = round(prev['close'], 2)
    pwh = round(max(b['high'] for b in week), 2)
    pwl = round(min(b['low'] for b in week), 2)
    pp  = round((pdh + pdl + pdc) / 3, 2)

    from src.cpr import compute_cpr, cpr_position
    cpr = compute_cpr(pdh, pdl, pdc)

    from core.shared_state import STATE
    price = STATE.get('market.price', 0) or pdc
    today_open = 0
    today_bars = [b for b in bars if b['date'] == today]
    if today_bars:
        today_open = today_bars[0]['open']
    cpr_pos = cpr_position(price, cpr, today_open)

    from src.expiry_picker import days_to_expiry
    expiry = STATE.get('zone', {}).get('expiry', '')
    dte = days_to_expiry(expiry) if expiry else 7
    now = datetime.now(IST)
    theta = compute_theta_risk(dte, now.hour, now.weekday())

    ctx.update({
        'available': True,
        'pdh': pdh, 'pdl': pdl, 'pdc': pdc,
        'pwh': pwh, 'pwl': pwl,
        'prev_range': round(pdh - pdl, 0),
        'pivot': pp,
        'r1': round(2 * pp - pdl, 2),
        's1': round(2 * pp - pdh, 2),
        'cpr': cpr,
        'cpr_position': cpr_pos,
        'theta': theta,
        'dte_days': dte,
    })
    return ctx


def refresh_market_context(token: str = '') -> dict:
    from core.shared_state import STATE
    ctx = build_market_context(token)
    STATE.set('market.context', ctx)
    return ctx


def format_context_report() -> str:
    from core.shared_state import STATE
    ctx = STATE.get('market.context') or build_market_context()
    if not ctx.get('available'):
        return "📊 *Market context*\n\nData not ready yet — refreshes during market hours."

    th = ctx.get('theta', {})
    cpr = ctx.get('cpr') or {}
    cpr_pos = ctx.get('cpr_position') or {}
    cpr_block = ""
    if cpr.get('available'):
        cpr_block = (
            f"\n*CPR:* TC {cpr['tc']:,.0f} | P {cpr['pivot']:,.0f} | BC {cpr['bc']:,.0f}\n"
            f"  Width: {cpr['width_class']} ({cpr['width_pct']:.2f}%)\n"
            f"  Position: {cpr_pos.get('zone', '?').replace('_', ' ')}"
        )
        if cpr_pos.get('virgin'):
            cpr_block += f" | *{cpr_pos['virgin'].replace('_', ' ')}*"

    return (
        f"📊 *Market Context* ({ctx.get('source', '?')} @ {ctx.get('updated', '')})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*Prior day:*\n"
        f"  PDH: {ctx['pdh']:,.0f} (resistance)\n"
        f"  PDL: {ctx['pdl']:,.0f} (support)\n"
        f"  Close: {ctx['pdc']:,.0f} | Range: {ctx['prev_range']:,.0f}\n\n"
        f"*Week (5d):*\n"
        f"  High: {ctx['pwh']:,.0f} | Low: {ctx['pwl']:,.0f}\n"
        f"{cpr_block}\n\n"
        f"*Floor pivots:* R1 {ctx['r1']:,.0f} | S1 {ctx['s1']:,.0f}\n\n"
        f"*Theta risk:* {th.get('score', 0)}/100 ({th.get('level', '?')}) | "
        f"Expiry in {ctx.get('dte_days', '?')}d\n"
        f"  {', '.join(th.get('notes', [])) or 'OK'}\n\n"
        f"_Send /cpr for full CPR report | /learn for RAG memory_"
    )
