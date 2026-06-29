"""9:25 AM trade plan one-liner appended to morning flow."""

from core.shared_state import STATE


def format_morning_trade_plan() -> str:
    zone = STATE.get('zone', {})
    price = STATE.get('market.price', 0)
    if not zone.get('active'):
        return (
            "\n📋 *Today's plan:* No evening zone — bot waits for structure.\n"
            "_Need flow ≥4 + zone touch + 10m confirm to fire._"
        )

    bias = zone.get('bias', 'NEUTRAL')
    low, high = zone.get('low', 0), zone.get('high', 0)
    em = '🟢' if bias == 'BULLISH' else '🔴'
    dist = ''
    if price and low and high:
        if price < low:
            dist = f' ({low - price:,.0f} pts to zone)'
        elif price > high:
            dist = f' ({price - high:,.0f} pts above zone)'
        else:
            dist = ' (in zone — confirm wait active)'

    flow = STATE.get('market.flow') or {}
    fs = flow.get('flow_score', '?')

    return (
        f"\n📋 *Today's plan:* {em} {bias} zone *{low:,.0f}–{high:,.0f}*"
        f"{dist}\n"
        f"  Need: flow ≥4 (now {fs}/6) | VWAP align | score ≥ brain min\n"
        f"  Sessions: 9:45–11:30 & 1:00–2:30 only | max 1 Execute/day"
    )
