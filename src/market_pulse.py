"""
Market pulse — short Telegram check-ins so you know the bot is alive.

Sent at fixed times during market hours (not spam — ~4/day).
"""

import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')

# Comma-separated hours IST, e.g. "10,11,13,14"
PULSE_HOURS = [
    int(h.strip()) for h in os.getenv(
        'ACTIVITY_PULSE_HOURS', '10,12,14'
    ).split(',') if h.strip().isdigit()
]
PULSE_ENABLED = os.getenv('ACTIVITY_PULSE', 'true').lower() == 'true'


def _zone_status(price: float, zone: dict) -> str:
    if not zone.get('active'):
        return 'No zone — evening scan ~8:15 PM'
    low, high = zone.get('low', 0), zone.get('high', 0)
    if not low or not high:
        return 'Zone saved — levels loading'
    mid = (low + high) / 2
    if low * 0.994 <= price <= high * 1.006:
        return f'🎯 *In zone* {low:,.0f}–{high:,.0f}'
    dist = (price - mid) / mid * 100 if mid else 0
    direction = 'above' if dist > 0 else 'below'
    return f'👀 {abs(dist):.2f}% {direction} zone ({low:,.0f}–{high:,.0f})'


def format_market_pulse() -> str:
    from core.shared_state import STATE

    now = datetime.now(IST)
    price = STATE.get('market.price', 0)
    source = STATE.get('market.data_source', '?')
    session = STATE.get('market.session', 'CLOSED')
    zone = STATE.get('zone', {})
    paused = STATE.get('system.paused', False)
    agents = STATE.get('system.agent_status', {})
    running = sum(1 for v in agents.values() if v == 'RUNNING')
    pos = STATE.get('position.open', False)

    lines = [
        f"💓 *Market Pulse* — {now.strftime('%I:%M %p IST')}",
        f"━━━━━━━━━━━━━━━━━━━",
        f"BNF: *{price:,.0f}* ({source}) | {session}",
        _zone_status(price, zone),
        f"Bot: {'⏸️ Paused' if paused else '▶️ Active'} | Agents {running}/7 ✅",
    ]

    try:
        from src.trade_filters import is_event_day
        event = is_event_day()
        if event.get('skip') or event.get('caution'):
            lines.append(f"⚠️ *Event day:* {event.get('reason', 'high impact')}")
    except Exception:
        pass

    if pos:
        lines.append(f"📌 Position: {STATE.get('position.name', 'open')}")

    try:
        from src.shadow_learning import learning_phase_info, get_today_shadow_trades
        info = learning_phase_info()
        shadows = get_today_shadow_trades()
        closed = [s for s in shadows if s.get('status') == 'CLOSED']
        open_s = [s for s in shadows if s.get('status') == 'OPEN']
        lines.append(
            f"🎓 Learning: {info['days_left']}d left | "
            f"Shadow today: {len(closed)} done, {len(open_s)} open"
        )
    except Exception:
        pass

    if zone.get('active') and not zone.get('used'):
        lines.append(
            "\n_Watching for pullback → CHoCH → setup. "
            "Quiet = filters working, not broken._"
        )
    elif not zone.get('active'):
        lines.append("\n_No zone tonight — bot scans at 8:15 PM._")
    else:
        lines.append("\n_Zone used today — standing by._")

    return '\n'.join(lines)


def should_send_pulse(hour: int, minute: int, last_sent_hour: int) -> bool:
    """True in the 5-minute window after each configured pulse hour."""
    if not PULSE_HOURS:
        return False
    if hour not in PULSE_HOURS:
        return False
    if minute > 5:
        return False
    if last_sent_hour == hour:
        return False
    return True
