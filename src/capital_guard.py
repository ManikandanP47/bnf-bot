"""
Capital Guard — Salary-professional risk limits
Protect capital first. Profit second.
"""

import os
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

MAX_DAILY_LOSS_RS   = float(os.getenv('MAX_DAILY_LOSS_RS', '5000'))
MAX_WEEKLY_LOSS_RS  = float(os.getenv('MAX_WEEKLY_LOSS_RS', '10000'))
MAX_LOT_SIZE        = int(os.getenv('MAX_LOT_SIZE', '1'))


def check_daily_loss_cap() -> dict:
    """Block new entries if today's loss exceeds cap."""
    from core.shared_state import STATE
    today_pnl = STATE.get('brain.today_pnl', 0)
    if today_pnl <= -MAX_DAILY_LOSS_RS:
        return {
            'blocked': True,
            'reason': (
                f'🛑 Daily loss cap hit (₹{today_pnl:,.0f}). '
                f'Max allowed: ₹{-MAX_DAILY_LOSS_RS:,.0f}. No more entries today.'
            ),
        }
    return {'blocked': False, 'reason': ''}


def check_weekly_loss_cap() -> dict:
    """Block if cumulative weekly P&L breaches cap."""
    from core.shared_state import STATE
    week_pnl = STATE.get('system.week_pnl', 0)
    if week_pnl <= -MAX_WEEKLY_LOSS_RS:
        return {
            'blocked': True,
            'reason': (
                f'🛑 Weekly loss cap hit (₹{week_pnl:,.0f}). '
                f'Bot paused until Monday. Capital protected.'
            ),
        }
    return {'blocked': False, 'reason': ''}


def compute_lots(kelly: float = 0.25, total_trades: int = 0) -> int:
    """
    Position size from brain Kelly fraction.
    Salary trader: always 1 lot until 30+ trades prove edge.
    Never exceed MAX_LOT_SIZE.
    """
    if total_trades < 30:
        return 1
    if kelly < 0.35:
        return 1
    if kelly < 0.55:
        return min(1, MAX_LOT_SIZE)
    return min(2, MAX_LOT_SIZE)


def format_morning_brief() -> str:
    """9:20 AM status — always know what the bot is doing."""
    from core.shared_state import STATE
    from src.zone_manager import zone_distance_pct

    now      = datetime.now(IST)
    zone     = STATE.get('zone', {})
    price    = STATE.get('market.price', 0)
    session  = STATE.get('market.session', 'CLOSED')
    paused   = STATE.get('system.paused', False)
    paper    = os.getenv('PAPER_MODE', 'true').lower() == 'true'
    trades   = STATE.get('brain.trades_today', 0)
    pnl      = STATE.get('brain.today_pnl', 0)
    w_loss   = STATE.get('system.weekly_losses', 0)
    brain    = STATE.get('brain', {})
    stage    = brain.get('learning_stage', 'EARLY')

    if zone.get('active'):
        dist = zone_distance_pct(price, zone) if price else None
        dist_txt = f'{dist:+.2f}% from zone' if dist is not None else 'waiting for price'
        zone_txt = (
            f"✅ *Zone active* ({zone.get('bias')})\n"
            f"  {zone.get('low', 0):,.0f}–{zone.get('high', 0):,.0f}\n"
            f"  {zone.get('option_name', '')}\n"
            f"  BNF {price:,.0f} — {dist_txt}"
        )
    else:
        zone_txt = (
            "❌ *No zone today*\n"
            "  Evening scan at 8:15 PM saves tomorrow's plan.\n"
            "  Bot will stay quiet until then."
        )

    windows = []
    t = now.time()
    from datetime import time as dtime
    if dtime(9, 45) <= t <= dtime(11, 30):
        windows.append('🟢 MORNING window open (9:45–11:30)')
    elif dtime(13, 0) <= t <= dtime(14, 0):
        windows.append('🟢 AFTERNOON window open (1:00–2:00 PM)')
    elif t < dtime(9, 45):
        windows.append('⏳ Trade windows: 9:45 AM & 1:00 PM')
    elif dtime(11, 30) < t < dtime(13, 0):
        windows.append('🍽 Lunch chop — no entries')
    elif t >= dtime(14, 0):
        windows.append('🔴 After 2 PM — no new entries')
    else:
        windows.append('⏳ Watching')

    pnl_e = '🟢' if pnl >= 0 else '🔴'
    mode  = 'Paper' if paper else 'Live'

    return (
        f"☀️ *Morning Brief — {now.strftime('%I:%M %p IST')}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Mode: {mode} | {'⏸ Paused' if paused else '▶️ Active'}\n"
        f"Session: {session}\n\n"
        f"{zone_txt}\n\n"
        f"{' | '.join(windows)}\n\n"
        f"Today: {trades} trade(s) | {pnl_e} ₹{pnl:,.0f}\n"
        f"Week losses: {w_loss}/2 | Brain: {stage}\n"
        f"Daily loss cap: ₹{MAX_DAILY_LOSS_RS:,.0f}\n\n"
        f"_Capital protection ON — quality over quantity_ 🛡️"
    )
