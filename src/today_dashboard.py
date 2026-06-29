"""
Today dashboard — /today command + context for LLM summary.
"""

from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def format_today_dashboard() -> str:
    from core.shared_state import STATE
    from src.paper_journal import get_today_trades, get_brain_stats
    from src.trade_analytics import format_funnel_report
    from src.shadow_learning import get_today_shadow_trades, learning_phase_info
    from src.market_pulse import _zone_status
    from src.trade_filters import is_event_day
    from src.safety import check_trading_day

    now = datetime.now(IST)
    price = STATE.get('market.price', 0)
    zone = STATE.get('zone', {})
    trades = get_today_trades()
    shadows = get_today_shadow_trades()
    info = learning_phase_info()
    stats = get_brain_stats()
    event = is_event_day()
    day = check_trading_day()

    lines = [
        f"📅 *Today* — {now.strftime('%d %b %Y %I:%M %p IST')}",
        "━━━━━━━━━━━━━━━━━━━",
        f"Market: {'🟢 Open' if STATE.get('system.market_open') else '🔴 Closed'} | "
        f"{day.get('reason', '')[:40]}",
        f"BNF: {price:,.0f} ({STATE.get('market.data_source', '?')})",
        _zone_status(price, zone),
        f"Bot: {'⏸️ Paused' if STATE.get('system.paused') else '▶️ Active'}",
    ]
    if event.get('skip') or event.get('caution'):
        lines.append(f"⚠️ Event: {event.get('reason', 'caution day')}")

    lines += [
        "",
        f"📝 Paper trades today: {len(trades)} | P&L ₹{sum(t.get('pnl_rs', 0) for t in trades):,.0f}",
        f"🎓 Shadow drills: {len(shadows)} "
        f"({sum(1 for s in shadows if s.get('outcome') == 'WIN')}W / "
        f"{sum(1 for s in shadows if s.get('outcome') == 'LOSS')}L)",
        f"🧠 Learning phase: {info['days_left']}d left | All-time {stats['total']} trades",
        "",
        format_funnel_report(),
    ]

    try:
        from src.llm_advisor import llm_enabled, summarize_day
        if llm_enabled():
            ctx = '\n'.join(lines)
            ai = summarize_day(ctx)
            if ai:
                lines += ["", "🤖 *AI coach:*", ai]
    except Exception:
        pass

    return '\n'.join(lines)
