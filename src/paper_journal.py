"""
Paper Journal — virtual trades in memory + SQLite brain
No Groww money needed. Every paper trade is tracked, learned, reported.
"""

import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')


def _conn():
    return sqlite3.connect(DB_FILE)


def get_today_trades() -> list:
    """All paper/live trades completed today from brain DB."""
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn  = _conn()
    rows  = conn.execute("""
        SELECT id, entry_time, exit_time, option_name, bias, session,
               entry_prem, exit_prem, pnl_rs, pnl_pct, outcome,
               exit_reason, lesson, mistake_type, score, hour
        FROM trades
        WHERE date = ? AND outcome IS NOT NULL
        ORDER BY id
    """, (today,)).fetchall()
    conn.close()
    keys = ['id', 'entry_time', 'exit_time', 'option_name', 'bias', 'session',
            'entry_prem', 'exit_prem', 'pnl_rs', 'pnl_pct', 'outcome',
            'exit_reason', 'lesson', 'mistake_type', 'score', 'hour']
    return [dict(zip(keys, r)) for r in rows]


def get_open_paper_trade() -> dict:
    """Trade entered today but not yet closed."""
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn  = _conn()
    row   = conn.execute("""
        SELECT id, entry_time, option_name, entry_prem, sl_prem, tgt_prem, bias, session
        FROM trades
        WHERE date = ? AND outcome IS NULL
        ORDER BY id DESC LIMIT 1
    """, (today,)).fetchone()
    conn.close()
    if not row:
        return {}
    keys = ['id', 'entry_time', 'option_name', 'entry_prem',
            'sl_prem', 'tgt_prem', 'bias', 'session']
    return dict(zip(keys, row))


def get_brain_stats() -> dict:
    """Overall learning progress for Telegram."""
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome IS NOT NULL"
    ).fetchone()[0]
    wins  = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome='WIN'"
    ).fetchone()[0]
    pnl   = conn.execute(
        "SELECT COALESCE(SUM(pnl_rs),0) FROM trades WHERE outcome IS NOT NULL"
    ).fetchone()[0]
    patterns = conn.execute(
        "SELECT COUNT(*) FROM pattern_memory WHERE samples >= 5"
    ).fetchone()[0]
    conn.close()
    wr = round(wins / total * 100, 1) if total else 0.0
    stage = f"EARLY ({total}/30)" if total < 30 else "ACTIVE"
    return {
        'total': total, 'wins': wins, 'losses': total - wins,
        'win_rate': wr, 'total_pnl': round(pnl, 0),
        'patterns_learned': patterns, 'stage': stage,
    }


def format_paper_entry(learning_id: int, params: dict, signal: dict) -> str:
    """Telegram when paper trade opens in memory."""
    stats = get_brain_stats()
    return (
        f"📝 *PAPER TRADE OPENED* (#{learning_id})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Option: *{params['name']}*\n"
        f"Entry:  ₹{params['premium']}/unit | Cost ₹{params['lot_cost']:,}\n"
        f"🛑 SL ₹{params['sl_prem']} | 🎯 Target ₹{params['tgt_prem']}\n"
        f"BNF at entry: {signal.get('price', 0):,.0f}\n"
        f"Session: {signal.get('session', '')} | Score {signal.get('score', 0)}\n\n"
        f"🧠 Logged to brain — tracking every 30s\n"
        f"Brain: {stats['total']} trades | {stats['win_rate']}% win rate\n"
        f"_Virtual money only — learning in progress_"
    )


def format_paper_exit(trade: dict, lesson: str, outcome: str,
                      pnl_rs: float, today_pnl: float) -> str:
    """Telegram when paper trade closes — what brain learned."""
    stats = get_brain_stats()
    from src.brain_metrics import compute_paper_confidence
    conf  = compute_paper_confidence()
    emoji = '🟢 WIN' if pnl_rs >= 0 else '🔴 LOSS'
    return (
        f"{emoji} *PAPER TRADE CLOSED* (#{trade.get('learning_id', '?')})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Option: {trade.get('name', '')}\n"
        f"Entry ₹{trade.get('entry_price', 0)} → Exit ₹{trade.get('exit_prem', 0):.0f}\n"
        f"P&L: *₹{pnl_rs:,.0f}* ({trade.get('pnl_pct', 0):+.1f}%)\n"
        f"Reason: {trade.get('exit_reason', '')}\n\n"
        f"🧠 *Brain learned:*\n  {lesson}\n\n"
        f"📊 *Scoreboard*\n"
        f"  Today: ₹{today_pnl:,.0f}\n"
        f"  All-time: {stats['total']} trades | {stats['win_rate']}% wins\n"
        f"  Total paper P&L: ₹{stats['total_pnl']:,}\n"
        f"  Confidence: {conf['score']}/100 ({conf['grade']})\n"
        f"  Stage: {stats['stage']}\n\n"
        f"_You + bot both learn from this trade_ 📚"
    )


def format_daily_paper_report() -> str:
    """End-of-day paper journal — sent ~3:35 PM."""
    now    = datetime.now(IST)
    trades = get_today_trades()
    open_t = get_open_paper_trade()
    stats  = get_brain_stats()
    from src.brain_metrics import compute_paper_confidence, assess_live_readiness
    conf   = compute_paper_confidence()
    ready  = assess_live_readiness()
    s      = conf['stats']

    lines  = [
        f"📓 *Daily Paper Journal — {now.strftime('%d %b %Y')}*",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    if not trades and not open_t:
        lines += [
            "",
            "📭 *No paper trade today*",
            "  Bot stayed disciplined — no setup passed all filters.",
            "  This is normal. Quality > quantity.",
        ]
    else:
        if open_t:
            lines += [
                "",
                f"⏳ *Still open:* {open_t['option_name']}",
                f"  Entered {open_t['entry_time']} @ ₹{open_t['entry_prem']}",
                f"  SL ₹{open_t['sl_prem']} | Target ₹{open_t['tgt_prem']}",
            ]
        total_day = 0
        for i, t in enumerate(trades, 1):
            e = '🟢' if t['outcome'] == 'WIN' else '🔴'
            total_day += t['pnl_rs'] or 0
            lines += [
                "",
                f"{e} *Trade {i}:* {t['option_name']}",
                f"  {t['entry_time']}→{t['exit_time']} | "
                f"₹{t['entry_prem']}→₹{t['exit_prem']}",
                f"  P&L: *₹{t['pnl_rs']:,.0f}* ({t['pnl_pct']:+.1f}%)",
                f"  Exit: {t['exit_reason']}",
                f"  🧠 {t['lesson'] or '—'}",
            ]
        day_e = '🟢' if total_day >= 0 else '🔴'
        lines += ["", f"{day_e} *Today's paper P&L: ₹{total_day:,.0f}*"]

    lines += [
        "",
        "🧠 *Brain progress*",
        f"  Total trades: {stats['total']}/30 (until adaptive learning)",
        f"  Win rate: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)",
        f"  All-time paper P&L: ₹{stats['total_pnl']:,}",
        f"  Patterns tracked: {stats['patterns_learned']}",
        f"  Target-hit rate: {s['efficiency_pct']}%",
        f"  Profit factor: {s['profit_factor']}",
        f"  Paper confidence: *{conf['score']}/100* ({conf['grade']})",
        "",
        f"🎯 *Live readiness:* {ready['reason']}",
        "",
        "_Review each trade above — you learn, brain learns_ 🤝",
    ]
    return '\n'.join(lines)


def format_journal_command() -> str:
    """For /journal — today's paper trades on demand."""
    return format_daily_paper_report()
