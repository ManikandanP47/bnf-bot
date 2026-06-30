"""Weekly training report — Sunday 8:15 PM IST Telegram summary."""

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')


def format_weekly_training_report() -> str:
    from src.shadow_learning import learning_phase_info, get_today_shadow_trades
    from src.sim_learning_report import get_sim_stats
    from src.valid_training_days import get_valid_day_counts
    from src.sim_evidence import get_daily_counts

    info = learning_phase_info()
    valid = get_valid_day_counts()
    week_ago = (datetime.now(IST) - timedelta(days=7)).strftime('%Y-%m-%d')
    stats = get_sim_stats(since_date=week_ago)

    lines = [
        f"📅 *Weekly Training Report*",
        f"_{datetime.now(IST).strftime('%d %b %Y')}_",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"*Phase:* {info['phase']}",
        f"*Valid days:* SIM {valid['sim_valid']}/{valid['sim_required']} | "
        f"paper {valid['paper_valid']}/{valid['paper_required']}",
        "",
        "*Last 7 days (virtual sim)*",
        f"  Trades closed: *{stats['total']}* | WR *{stats['win_rate']}%*",
        f"  Virtual P&L: ₹{stats['total_pnl']:,}",
        f"  Avg hold: {stats['avg_hold_min']}m",
    ]

    if stats.get('by_session'):
        lines.append("\n*By session:*")
        for sess, d in sorted(stats['by_session'].items()):
            wr = round(d['w'] / d['n'] * 100, 1) if d['n'] else 0
            lines.append(f"  {sess}: {d['n']} | {wr}% WR")

    try:
        from src.ml_brain import format_ml_status
        lines += ["", format_ml_status()]
    except Exception:
        pass

    try:
        from src.brain_metrics import assess_live_readiness
        r = assess_live_readiness()
        lines += ["", f"🎯 Live gates: {r['reason']}"]
    except Exception:
        pass

    if info['phase'] == 'SIM' and valid['sim_valid'] < 3:
        lines.append(
            "\n⚠️ _Few valid sim days this week — check /evidence daily_"
        )

    lines.append("\n_/training for full dashboard_")
    return '\n'.join(lines)
