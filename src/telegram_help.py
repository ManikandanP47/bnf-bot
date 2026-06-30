"""Telegram command reference — startup summary + full /help text."""


def format_startup_message(paper: bool) -> str:
    mode = '📝 Paper (confirm each trade)' if paper else '💸 Live'
    try:
        from src.training_calendar import (
            TRAINING_START_DATE, TRAINING_MONTH_DAYS, training_day_number,
            verify_training_stack, is_pre_training,
        )
        d = training_day_number()
        v = verify_training_stack()
        stack = '✅' if v.get('all_ok') else '⚠️'
        if is_pre_training():
            plan = f"⏳ Training starts *{TRAINING_START_DATE}* ({TRAINING_MONTH_DAYS} days)"
        elif d > 0:
            plan = f"🎓 *July training day {d}/{TRAINING_MONTH_DAYS}* {stack}"
        else:
            plan = f"🎓 *{TRAINING_MONTH_DAYS}-day training* {stack}"
    except Exception:
        plan = '🎓 *July training month* — sim → paper → live'
    return (
        f"🚀 *Multi-Agent Bot Started*\n\n"
        f"Mode: {mode}\n"
        f"Agents: All 9 running ✅\n"
        f"{plan}\n"
        f"Pre-market: 9:00 | Observer: 9:16 | Flow: 9:25 | Evening: 8:15 PM IST\n\n"
        f"*July plan*\n"
        f"  Jul 1–15: SIM — ₹10k wallet, multi-order, recovery, Greeks\n"
        f"  Jul 16–31: PAPER `/execute` (paper mode stays true)\n"
        f"  Aug 1+: Live only if `/readiness` ✅\n\n"
        f"📊 Dashboard: Sim & Learning tab\n"
        f"🎓 /training /recovery /shadow /simday\n\n"
        f"_No live ₹5k until training month + gates pass._ 🛡️"
    )


def format_full_help() -> str:
    return (
        "🤖 *BNF Bot — All Commands*\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "*Control*\n"
        "/pause — Stop new entries today; open positions still monitored\n"
        "/resume — Resume watching for setups\n"
        "/stop — Emergency stop; blocks entries until /resume tomorrow\n\n"
        "*Trading*\n"
        "/execute — Confirm pending trade suggestion (paper or live)\n"
        "/skip — Skip pending trade; logged for skip-learning at EOD\n\n"
        "*Status & P&L*\n"
        "/status — Agents, BNF price, position, brain stats, feed health\n"
        "/pnl — Today's paper P&L summary\n"
        "/zone — Tonight's support/resistance zone from evening scan\n\n"
        "*Paper & live gates*\n"
        "/journal — Today's paper trades + brain lessons + confidence\n"
        "/readiness — 8+ gate checklist before live ₹5k (WR, P&L, drawdown)\n"
        "/funnel — Signal funnel: what passed/failed filters today\n\n"
        "*Market context*\n"
        "/context — PDH/PDL, theta decay, pivot levels, regime\n"
        "/cpr — Central Pivot Range (TC / P / BC)\n"
        "/flow — OI, PCR, VIX, EMA, theta, chart lines (auto 9:25 AM)\n"
        "/today — Full day dashboard + OpenAI coach summary\n\n"
        "*Virtual training (live market, no money)*\n"
        "/training — Unified dashboard: phase, valid days, evidence, readiness\n"
        "/shadow — Today's virtual CE/PE drills + learning phase status\n"
        "/simday — Full sim scan log: what it saw, skipped, scored today\n"
        "/evidence — Auditable DB + JSONL counts (valid/invalid training day)\n"
        "/simreport — Daily training digest or 2-week graduation WR\n"
        "/ml — ML status: RF at 25+ samples, neural net auto at 100+\n"
        "/learn — RAG memory: rules + lessons from your trades\n"
        "/resetlearning — Clear graduation flag to re-send phase-end report\n\n"
        "*Diagnostics*\n"
        "/groww — Groww API health, rate limits, WebSocket feed status\n"
        "/why — Why the last setup was blocked (filters explained)\n"
        "/backtest — Historical proxy backtest on past setups\n\n"
        "/help — This message\n\n"
        "_Virtual sim (wk 1–2) → paper /execute (wk 3–4) → live after /readiness ✅_ 🛡️"
    )
