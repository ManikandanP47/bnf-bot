"""
Market flow — unified F&O dashboard.

Combines what discretionary traders check:
  OI (PCR, max pain, CE/PE walls)
  VIX fear gauge
  EMA20/50 trend
  VWAP position
  Theta estimate
  CPR / PDH / PDL
  Auto chart S/R lines
"""

import time
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
OI_CACHE_SEC = 300


def estimate_theta_bleed(premium: float, dte_days: int, hour: int) -> dict:
    """
    Simplified theta model for option buyers (not Black-Scholes).
    Higher % = more premium lost per day if price stays flat.
    """
    if premium <= 0 or dte_days <= 0:
        return {'daily_pct': 0, 'level': 'UNKNOWN'}

    base = 4.0 / max(dte_days, 1)
    if dte_days <= 2:
        base *= 2.2
    elif dte_days <= 5:
        base *= 1.4
    if hour >= 13:
        base *= 1.5
    elif hour >= 11:
        base *= 1.2

    daily_rs = round(premium * base / 100 * 15, 0)
    level = 'LOW' if base < 5 else 'MEDIUM' if base < 9 else 'HIGH'
    return {
        'daily_pct': round(base, 1),
        'daily_rs_per_lot': daily_rs,
        'level': level,
        'note': f'~{base:.1f}% premium/day if BNF flat (~₹{daily_rs}/lot)',
    }


def build_market_flow(price: float = 0, bias: str = 'BULLISH') -> dict:
    """Aggregate all flow metrics into one snapshot."""
    from core.shared_state import STATE
    from src.market_validator import check_vix, check_oi, check_ema

    price = price or STATE.get('market.price', 0)
    vix = check_vix()
    oi  = check_oi(bias, price) if price else {'status': 'UNKNOWN', 'score': 0}
    ema = check_ema(bias)

    ctx = STATE.get('market.context') or {}
    vwap = STATE.get('market.vwap', 0)
    vwap_pos = 'AT'
    if vwap and price:
        if price > vwap * 1.001:
            vwap_pos = 'ABOVE'
        elif price < vwap * 0.999:
            vwap_pos = 'BELOW'

    from src.chart_levels import compute_chart_levels
    c5  = STATE.get('market.candles_5m', [])
    c15 = STATE.get('market.candles_15m', [])
    chart = compute_chart_levels(c5, c15, price)

    dte = ctx.get('dte_days', 7)
    premium = (STATE.get('zone') or {}).get('premium', 200)
    theta_est = estimate_theta_bleed(premium, dte, datetime.now(IST).hour)
    oi_deep = _get_oi_deep_cached()

    flow_score = 0
    flow_score += vix.get('score', 0)
    flow_score += oi.get('score', 0)
    flow_score += ema.get('score', 0)

    blocked = []
    if vix.get('status') == 'BLOCK':
        blocked.append(vix.get('reason', 'VIX block'))
    if ema.get('status') == 'BLOCK':
        blocked.append(ema.get('reason', 'EMA block'))

    snap = {
        'available':   True,
        'updated':     datetime.now(IST).strftime('%H:%M'),
        'price':       price,
        'bias':        bias,
        'vix':         vix,
        'oi':          oi,
        'oi_deep':     oi_deep,
        'ema':         ema,
        'vwap':        vwap,
        'vwap_pos':    vwap_pos,
        'theta_est':   theta_est,
        'context':     ctx,
        'chart':       chart,
        'flow_score':  flow_score,
        'blocked':     blocked,
    }
    return snap


def _fetch_oi_deep() -> dict:
    from core.shared_state import STATE
    try:
        from src.oi_analysis import get_oi_data, calculate_max_pain
        raw = get_oi_data()
        if raw:
            mp = calculate_max_pain(raw)
            if mp.get('available'):
                STATE.set('market.oi_deep', mp)
                STATE.set('market.oi_deep_ts', time.time())
                return mp
    except Exception:
        pass
    return {}


def _get_oi_deep_cached() -> dict:
    from core.shared_state import STATE
    cached = STATE.get('market.oi_deep') or {}
    ts = STATE.get('market.oi_deep_ts', 0)
    if cached and time.time() - ts < OI_CACHE_SEC:
        return cached
    return cached


def refresh_market_flow(bias: str = 'BULLISH') -> dict:
    from core.shared_state import STATE
    _fetch_oi_deep()
    snap = build_market_flow(STATE.get('market.price', 0), bias)
    STATE.set('market.flow', snap)
    return snap


def flow_allows_trade(bias: str, price: float) -> dict:
    """Hard + soft checks before trade."""
    flow = build_market_flow(price, bias)
    if flow.get('blocked'):
        return {
            'ok': False,
            'reason': flow['blocked'][0],
            'flow': flow,
        }

    oi = flow.get('oi', {})
    if bias == 'BULLISH' and oi.get('status') == 'AGAINST':
        return {
            'ok': False,
            'reason': oi.get('reason', 'OI against bullish trade'),
            'flow': flow,
        }
    if bias == 'BEARISH' and oi.get('status') == 'AGAINST':
        return {
            'ok': False,
            'reason': oi.get('reason', 'OI against bearish trade'),
            'flow': flow,
        }

    th = (flow.get('context') or {}).get('theta', {})
    if th.get('score', 0) > 60:
        return {
            'ok': False,
            'reason': f"Theta risk {th.get('score')}/100 — F&O flow says wait",
            'flow': flow,
        }

    return {'ok': True, 'reason': '', 'flow': flow}


def format_flow_report() -> str:
    from core.shared_state import STATE
    flow = STATE.get('market.flow') or build_market_flow()
    if not flow.get('available'):
        return "🌊 *Market Flow*\n\nLoading… refreshes during market hours."

    vix = flow.get('vix', {})
    oi  = flow.get('oi', {})
    ema = flow.get('ema', {})
    od  = flow.get('oi_deep', {})
    th  = flow.get('theta_est', {})
    ch  = flow.get('chart', {})
    ctx = flow.get('context', {})

    lines = [
        f"🌊 *Market Flow* @ {flow.get('updated', '?')} IST",
        f"━━━━━━━━━━━━━━━━━━━",
        f"BNF: {flow.get('price', 0):,.0f} | Flow score: {flow.get('flow_score', 0)}/6",
        "",
        "*Fear (VIX):*",
        f"  {vix.get('reason', '—')}",
        "",
        "*Open Interest:*",
        f"  {oi.get('reason', '—')}",
    ]
    if od:
        lines += [
            f"  PCR {od.get('pcr', '?')} ({od.get('pcr_signal', '?')})",
            f"  Max Pain: {od.get('max_pain', 0):,}",
            f"  CE wall (resistance): {od.get('resistance', 0):,}",
            f"  PE wall (support): {od.get('support', 0):,}",
        ]
    lines += [
        "",
        "*Trend (EMA20/50 daily):*",
        f"  {ema.get('reason', '—')}",
        "",
        f"*VWAP:* price {flow.get('vwap_pos', '?')} VWAP ({flow.get('vwap', 0):,.0f})",
        "",
        "*Theta (option buyer):*",
        f"  {th.get('note', '—')}",
    ]
    if ctx.get('cpr', {}).get('available'):
        cpr = ctx['cpr']
        lines.append(
            f"\n*CPR:* TC {cpr['tc']:,.0f} | BC {cpr['bc']:,.0f} "
            f"({cpr['width_class']})"
        )
    if ch.get('available'):
        lines += [
            "",
            "*Chart lines (auto):*",
            f"  15m resistance: {ch.get('resistance_15m') or '—'}",
            f"  15m support:    {ch.get('support_15m') or '—'}",
            f"  Swing trend: {ch.get('swing_trend', '?')}",
        ]
        if ch.get('trend_note'):
            lines.append(f"  _{ch['trend_note']}_")

    lines.append("\n_Bot uses all of this before suggesting a trade_")
    return '\n'.join(lines)


def format_flow_compact(flow: dict) -> str:
    """Short block for trade suggestion Telegram."""
    if not flow or not flow.get('available'):
        return ''
    oi = flow.get('oi', {})
    od = flow.get('oi_deep', {})
    vix = flow.get('vix', {})
    th = flow.get('theta_est', {})
    lines = ["\n🌊 *F&O Flow:*"]
    if vix.get('vix'):
        lines.append(f"  VIX {vix['vix']} ({vix.get('status', '?')})")
    if oi.get('pcr'):
        lines.append(f"  PCR {oi['pcr']} ({oi.get('status', '?')})")
    elif od.get('pcr'):
        lines.append(f"  PCR {od['pcr']} | MaxPain {od.get('max_pain', 0):,}")
    if od.get('resistance'):
        lines.append(f"  CE OI wall: {od['resistance']:,} | PE support: {od.get('support', 0):,}")
    if th.get('note'):
        lines.append(f"  Theta: {th['note']}")
    ch = flow.get('chart', {})
    if ch.get('resistance_15m'):
        lines.append(f"  Chart res: {ch['resistance_15m']:,.0f} | sup: {ch.get('support_15m', 0):,.0f}")
    return '\n'.join(lines)


def format_morning_flow_telegram() -> str:
    """Auto 9:25 AM summary — refreshes OI/VIX/EMA and posts to Telegram."""
    from core.shared_state import STATE

    zone = STATE.get('zone') or {}
    bias = zone.get('bias', 'BULLISH')
    refresh_market_flow(bias)

    header = "☀️ *Morning F&O Flow* — auto 9:25 AM\n"
    if zone.get('active'):
        header += (
            f"📌 Zone: {zone.get('low', 0):,.0f}–{zone.get('high', 0):,.0f} "
            f"({zone.get('bias', '?')}) — watching for pullback\n\n"
        )
    else:
        header += "📌 No active zone — bot stays quiet unless evening scan saves one\n\n"

    return header + format_flow_report()
