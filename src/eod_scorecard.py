"""Enhanced EOD scorecard — blocks, shadow, funnel summary."""

from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def format_eod_scorecard() -> str:
    lines = ['', '📊 *EOD Scorecard*', '━━━━━━━━━━━━━━━━━━━']

    try:
        from src.trade_analytics import format_funnel_report
        funnel = format_funnel_report()
        if funnel:
            lines.append(funnel)
    except Exception:
        pass

    try:
        from src.shadow_learning import format_shadow_daily_section
        lines.append(format_shadow_daily_section())
    except Exception:
        pass

    try:
        from src.skip_learning import format_skip_weekly_section
        sec = format_skip_weekly_section()
        if sec:
            lines.append(sec)
    except Exception:
        pass

    try:
        from src.learning_scoreboard import format_scoreboard
        lines.append('')
        lines.append(format_scoreboard(7))
    except Exception:
        pass

    return '\n'.join(lines)
