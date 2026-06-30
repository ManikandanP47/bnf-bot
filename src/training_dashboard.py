"""Unified /training dashboard — one view of phase, evidence, validity, readiness."""

from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def format_training_dashboard() -> str:
    from src.shadow_learning import learning_phase_info, get_today_shadow_trades
    from src.sim_evidence import format_evidence_report, get_daily_counts, is_training_day_valid
    from src.valid_training_days import get_valid_day_counts, evaluate_day
    from src.sim_scan_journal import format_sim_day_visibility
    from collections import Counter

    info = learning_phase_info()
    valid = get_valid_day_counts()
    today_ev = evaluate_day()
    audit = is_training_day_valid()
    counts = get_daily_counts()

    cal_line = ''
    try:
        from src.training_calendar import (
            TRAINING_START_DATE, TRAINING_MONTH_DAYS, training_day_number,
            training_week_number, verify_training_stack,
        )
        d = training_day_number()
        cal_line = (
            f"*Calendar:* {TRAINING_START_DATE} → {TRAINING_MONTH_DAYS}d | "
            f"day *{d or 'pre-start'}* | week *{training_week_number()}*"
        )
        v = verify_training_stack()
        if not v.get('all_ok'):
            cal_line += f"\n⚠️ Stack: {len(v.get('failed', []))} check(s) — restart after deploy"
    except Exception:
        pass

    lines = [
        f"🎓 *Training Dashboard — {datetime.now(IST).strftime('%d %b %Y %I:%M %p')}*",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        cal_line,
        f"*Phase:* {info['phase']} | elapsed *{info['elapsed_days']}* days",
        f"*Valid days:* SIM *{valid['sim_valid']}/{valid['sim_required']}* | "
        f"paper *{valid['paper_valid']}/{valid['paper_required']}*",
        f"Paper unlocks: *{info['days_until_paper']}d* | Live window: *{info['days_until_live']}d*",
        "",
        "*Today (evidence)*",
        f"  Scans: *{counts['scans_total']}* | sim trades: *{counts['shadow_opened']}* | "
        f"ticks: *{counts['sim_ticks']}*",
        f"  Today valid: *{'✅ yes' if today_ev['sim_valid'] or today_ev['paper_valid'] else '❌ no'}* "
        f"— {today_ev['reason']}",
    ]

    if not audit['valid'] and info['phase'] == 'SIM':
        lines.append("  ⚠️ *Invalid training day* until scans are logged")

    try:
        from src.sim_scan_journal import get_today_scans
        skips = Counter(
            s['reason'] for s in get_today_scans()
            if s['event'] == 'SKIP' and s.get('reason')
        )
        if skips:
            lines.append("\n*Top skip reasons today:*")
            for reason, n in skips.most_common(3):
                lines.append(f"  • {reason}: {n}×")
    except Exception:
        pass

    try:
        from src.market_observer import get_current_session
        sess = get_current_session()
        lines.append(
            f"\n*Session quality:* {sess.get('session', '?')} — "
            f"{sess.get('quality', '?')} "
            f"({'tradeable' if sess.get('tradeable') else 'avoid'})"
        )
    except Exception:
        pass

    try:
        from src.sim_execute_gap import format_execute_gap_summary
        gap = format_execute_gap_summary()
        if gap:
            lines += ["", "*Sim vs Execute path:*", gap]
    except Exception:
        pass

    try:
        from src.ml_brain import format_ml_status
        lines += ["", format_ml_status()]
    except Exception:
        pass

    try:
        from src.sim_wallet import wallet_core
        w = wallet_core()
        lines += [
            "",
            f"*Sim wallet:* ₹{w.get('balance', 0):,.0f} "
            f"(W{w.get('week')} base ₹{w.get('week_base_rs', 0):,.0f})",
            f"  Lots {w.get('lots_allowed')}/{w.get('max_open')} open | "
            f"recovery ON | today P&L ₹{w.get('week_pnl', 0):+,.0f}",
        ]
    except Exception:
        pass

    try:
        from src.brain_metrics import assess_live_readiness
        r = assess_live_readiness()
        lines += ["", f"🎯 *Live readiness:* {r['reason']}"]
    except Exception:
        pass

    shadows = get_today_shadow_trades()
    if shadows:
        lines.append(f"\n*Today's sims:* {len(shadows)}")
        for t in shadows[:3]:
            st = t.get('status', '')
            lines.append(f"  #{t['id']} {t.get('option_name', '')} — {st}")

    lines += [
        "",
        "_Commands: /evidence /simday /shadow /readiness /journal_",
    ]
    return '\n'.join(lines)
