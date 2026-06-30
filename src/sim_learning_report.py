"""
Sim Learning Report — daily training digest + 2-week graduation.

Bot trains on LIVE market (real BNF + real option LTP when Groww available).
No orders placed. Every sim is logged with entry/exit premium and P&L.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
LEARNING_PHASE_DAYS = int(os.getenv('LEARNING_PHASE_DAYS', '14'))
MIN_SIM_GRAD_SAMPLES = int(os.getenv('MIN_SIM_GRAD_SAMPLES', '20'))
MIN_SIM_GRAD_WR = float(os.getenv('MIN_SIM_GRAD_WR', os.getenv('SHADOW_MIN_WR', '40')))


def _conn():
    return sqlite3.connect(DB_FILE)


def _hold_minutes(entry_t: str, exit_t: str) -> int:
    if not entry_t or not exit_t:
        return 0
    try:
        e = datetime.strptime(entry_t, '%H:%M')
        x = datetime.strptime(exit_t, '%H:%M')
        return max(0, int((x - e).total_seconds() / 60))
    except ValueError:
        return 0


def get_sim_stats(since_date: str = None) -> dict:
    """Aggregate virtual sim performance."""
    conn = _conn()
    q = """
        SELECT outcome, pnl_rs, session, bias, entry_time, exit_time,
               entry_prem, exit_prem, exit_reason, sim_source, mae_prem,
               mfe_prem, peak_pnl_rs, prem_source
        FROM shadow_trades WHERE outcome IS NOT NULL
    """
    args = []
    if since_date:
        q += " AND date >= ?"
        args.append(since_date)
    rows = conn.execute(q, args).fetchall()
    conn.close()

    if not rows:
        return {
            'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0,
            'total_pnl': 0, 'avg_win': 0, 'avg_loss': 0,
            'avg_hold_min': 0, 'live_prem_pct': 0.0,
            'by_session': {}, 'exit_reasons': {},
        }

    wins = [r for r in rows if r[0] == 'WIN']
    losses = [r for r in rows if r[0] == 'LOSS']
    holds = [_hold_minutes(r[4], r[5]) for r in rows if r[5]]
    live_prem = sum(1 for r in rows if (r[13] or '') == 'GROWW_LTP')

    by_sess = {}
    for r in rows:
        sess = r[2] or 'UNKNOWN'
        by_sess.setdefault(sess, {'n': 0, 'w': 0, 'pnl': 0})
        by_sess[sess]['n'] += 1
        if r[0] == 'WIN':
            by_sess[sess]['w'] += 1
        by_sess[sess]['pnl'] += r[1] or 0

    exits = {}
    for r in rows:
        reason = (r[8] or 'other')[:30]
        exits[reason] = exits.get(reason, 0) + 1

    gross_win = sum(r[1] for r in wins)
    gross_loss = abs(sum(r[1] for r in losses))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0

    return {
        'total': len(rows),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(rows) * 100, 1),
        'total_pnl': round(sum(r[1] or 0 for r in rows), 0),
        'avg_win': round(gross_win / len(wins), 0) if wins else 0,
        'avg_loss': round(gross_loss / len(losses), 0) if losses else 0,
        'profit_factor': pf,
        'avg_hold_min': round(sum(holds) / len(holds), 0) if holds else 0,
        'live_prem_pct': round(live_prem / len(rows) * 100, 0),
        'by_session': by_sess,
        'exit_reasons': exits,
    }


def get_shadow_patterns(min_samples: int = 5) -> list:
    """Top learned patterns from sim training."""
    conn = _conn()
    rows = conn.execute("""
        SELECT pattern_key, wins, losses, samples, total_pnl
        FROM pattern_memory
        WHERE pattern_key LIKE 'shadow:%' AND samples >= ?
        ORDER BY samples DESC LIMIT 8
    """, (min_samples,)).fetchall()
    conn.close()
    out = []
    for key, w, l, n, pnl in rows:
        wr = round(w / (w + l) * 100, 1) if (w + l) else 0
        label = key.replace('shadow:', '')
        out.append({'key': label, 'wr': wr, 'samples': n, 'pnl': pnl})
    return out


def format_daily_sim_training_report() -> str:
    """
    Daily Telegram digest — every virtual trade on real market today.
    Sent after market close; no Groww orders were placed.
    """
    from src.shadow_learning import (
        get_today_shadow_trades, learning_phase_info, init_shadow_tables,
    )
    init_shadow_tables()

    now = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')
    trades = get_today_shadow_trades()
    info = learning_phase_info()
    phase_stats = get_sim_stats(since_date=_phase_start_date())

    closed = [t for t in trades if t.get('status') == 'CLOSED']
    open_t = [t for t in trades if t.get('status') == 'OPEN']
    day_pnl = sum(t.get('pnl_rs') or 0 for t in closed)
    day_wins = sum(1 for t in closed if t.get('outcome') == 'WIN')

    lines = [
        f"🎮 *Daily Market Training — {now.strftime('%d %b %Y')}*",
        "━━━━━━━━━━━━━━━━━━━",
        "_Bot trained on LIVE BNF + option premium — zero orders placed_",
        "",
        f"🎓 Phase: *{info['phase']}* — {info['days_left']}d left "
        f"(paper in {info['days_until_paper']}d | live window in {info['days_until_live']}d)",
    ]

    if not trades:
        lines += [
            "",
            "📭 No virtual trades today.",
            f"_Cumulative sim WR: {phase_stats['win_rate']}% "
            f"({phase_stats['total']} drills)_",
        ]
        try:
            from src.sim_scan_journal import format_sim_day_visibility
            vis = format_sim_day_visibility(compact=True)
            # Skip duplicate header — append body after first line block
            vis_body = '\n'.join(vis.split('\n')[2:])
            lines += ["", vis_body]
        except Exception:
            lines.append("Bot scanned live flow but no setup scored high enough.")
        return '\n'.join(lines)

    lines.append(f"\n📋 *Today's sims: {len(closed)} closed | {len(open_t)} open*")

    for t in trades:
        src = t.get('sim_source') or 'SIM'
        prem_src = t.get('prem_source') or 'model'
        prem_tag = '📡 live LTP' if prem_src == 'GROWW_LTP' else '📐 delta model'

        if t.get('status') == 'OPEN':
            lines += [
                "",
                f"⏳ *#{t['id']}* {t['option_name']} ({src})",
                f"  Entry {t['entry_time']} @ ₹{t['entry_prem']} ({prem_tag})",
                f"  BNF range: {t.get('range_note') or '—'}",
                f"  _Still tracking live premium…_",
            ]
            continue

        e = '🟢' if t.get('outcome') == 'WIN' else '🔴'
        hold = _hold_minutes(t.get('entry_time'), t.get('exit_time'))
        lines += [
            "",
            f"{e} *#{t['id']}* {t['option_name']} ({src})",
            f"  Entry {t['entry_time']} @ ₹{t['entry_prem']} ({prem_tag})",
            f"  Exit  {t['exit_time']} @ ₹{t['exit_prem']} | held {hold}m",
            f"  P&L: *₹{t['pnl_rs']:,.0f}* | peak ₹{t.get('peak_pnl_rs') or 0:,.0f}",
            f"  Exit: {t.get('exit_reason', '—')}",
            f"  🧠 {t.get('lesson', '—')[:90]}",
        ]

    day_e = '🟢' if day_pnl >= 0 else '🔴'
    day_wr = round(day_wins / len(closed) * 100, 1) if closed else 0
    lines += [
        "",
        f"{day_e} *Today virtual P&L: ₹{day_pnl:,.0f}* | {day_wr}% win ({day_wins}/{len(closed)})",
        "",
        "📊 *2-week training progress*",
        f"  Total sims: {phase_stats['total']} | WR: *{phase_stats['win_rate']}%*",
        f"  Virtual P&L: ₹{phase_stats['total_pnl']:,}",
        f"  Avg hold: {phase_stats['avg_hold_min']}m | "
        f"live premium on {phase_stats['live_prem_pct']}% entries",
        f"  Avg win ₹{phase_stats['avg_win']:,} | Avg loss ₹{phase_stats['avg_loss']:,}",
    ]

    if phase_stats['by_session']:
        lines.append("\n*Session learning:*")
        for sess, d in sorted(phase_stats['by_session'].items()):
            wr = round(d['w'] / d['n'] * 100, 1) if d['n'] else 0
            lines.append(f"  {sess}: {d['n']} sims | {wr}% WR | ₹{d['pnl']:,.0f}")

    patterns = get_shadow_patterns(3)
    if patterns:
        lines.append("\n*Patterns bot is learning:*")
        for p in patterns[:4]:
            lines.append(f"  {p['key']}: {p['wr']}% ({p['samples']} samples)")

    try:
        from src.sim_scan_journal import get_today_scans
        from collections import Counter
        scans = [s for s in get_today_scans() if s['event'] != 'COOLDOWN']
        if scans:
            skips = Counter(s['reason'] for s in scans if s['event'] == 'SKIP')
            lines.append(f"\n🔍 *Scan log:* {len(scans)} evaluations today")
            for reason, n in skips.most_common(3):
                lines.append(f"  skipped {reason}: {n}×")
    except Exception:
        pass

    lines.append(
        "\n_Type /simday for full scan log | /shadow | /readiness before live ₹5k_"
    )
    return '\n'.join(lines)


def _phase_start_date() -> str:
    from src.shadow_learning import init_shadow_tables
    init_shadow_tables()
    conn = _conn()
    row = conn.execute("""
        SELECT MIN(date) FROM (
            SELECT date FROM shadow_trades
            UNION SELECT date FROM trades WHERE outcome IS NOT NULL
        )
    """).fetchone()
    conn.close()
    return row[0] if row and row[0] else datetime.now(IST).strftime('%Y-%m-%d')


def format_sim_phase_complete_report() -> str:
    """End of week 2 — virtual sim done, paper phase starts."""
    from src.shadow_learning import PAPER_PHASE_DAYS, TOTAL_TRAINING_DAYS
    stats = get_sim_stats(since_date=_phase_start_date())
    cap = int(os.getenv('LEARNING_MAX_TRADES_DAY', '2'))

    lines = [
        "🎓 *Week 1–2 complete — virtual sim finished*",
        "━━━━━━━━━━━━━━━━━━━",
        f"  Virtual trades: *{stats['total']}* | WR *{stats['win_rate']}%*",
        f"  Virtual P&L: ₹{stats['total_pnl']:,} (not real money)",
        "",
        f"📝 *Week 3–4: paper training starts tomorrow*",
        f"  • Bot suggests setups — you `/execute` or `/skip`",
        f"  • Max *{cap}* confirmed paper trades/day",
        f"  • ML keeps learning from paper closes (RF + NN)",
        f"  • Virtual sim is *OFF* — paper uses slippage model",
        "",
        f"🎯 After *{TOTAL_TRAINING_DAYS}* days total + `/readiness` green → live ₹5k",
        f"_Paper phase: {PAPER_PHASE_DAYS} days — track `/journal` daily_",
    ]
    try:
        from src.ml_brain import format_ml_status
        lines += ["", format_ml_status()]
    except Exception:
        pass
    return '\n'.join(lines)


def format_graduation_report() -> str:
    """4-week training complete — sim + paper summary, live readiness."""
    from src.shadow_learning import learning_phase_info
    info = learning_phase_info()
    stats = get_sim_stats(since_date=_phase_start_date())
    patterns = get_shadow_patterns(5)

    ready_wr = stats['win_rate'] >= MIN_SIM_GRAD_WR
    ready_n = stats['total'] >= MIN_SIM_GRAD_SAMPLES
    ready = ready_wr and ready_n

    lines = [
        "🎓 *4-Week Training Complete — Month-End Report*",
        "━━━━━━━━━━━━━━━━━━━",
        "_Bot trained on real market flow — no money was placed_",
        "",
        "📊 *Overall sim performance*",
        f"  Virtual trades: *{stats['total']}*",
        f"  Win rate: *{stats['win_rate']}%* ({stats['wins']}W / {stats['losses']}L)",
        f"  Virtual P&L: ₹{stats['total_pnl']:,}",
        f"  Profit factor: {stats.get('profit_factor', 0)}",
        f"  Avg win: ₹{stats['avg_win']:,} | Avg loss: ₹{stats['avg_loss']:,}",
        f"  Avg hold: {stats['avg_hold_min']} minutes",
        f"  Real Groww LTP used: {stats['live_prem_pct']}% of entries",
    ]

    if stats['by_session']:
        lines.append("\n*Best / worst sessions (timing):*")
        ranked = sorted(
            stats['by_session'].items(),
            key=lambda x: x[1]['w'] / x[1]['n'] if x[1]['n'] else 0,
            reverse=True,
        )
        for sess, d in ranked:
            wr = round(d['w'] / d['n'] * 100, 1) if d['n'] else 0
            lines.append(f"  {sess}: {wr}% WR ({d['n']} sims) | ₹{d['pnl']:,.0f}")

    if stats['exit_reasons']:
        lines.append("\n*Exit behaviour learned:*")
        for reason, n in sorted(stats['exit_reasons'].items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {reason}: {n}×")

    if patterns:
        lines.append("\n*Strongest patterns in memory:*")
        for p in sorted(patterns, key=lambda x: -x['wr'])[:5]:
            lines.append(f"  ✅ {p['key']}: {p['wr']}% ({p['samples']} samples)")

    lines.append("\n🛡️ *Risk understanding (before live ₹5k)*")
    if stats['total'] >= 5:
        lines += [
            f"  Max single sim loss: ₹{stats['avg_loss']:,} typical",
            f"  Bot knows to exit on SL, target, flow fade, or EOD",
            f"  ₹5k capital = max 1 lot, 25% risk per trade enforced",
        ]
    else:
        lines.append("  ⚠️ Too few sims — extend training before live.")

    if ready:
        lines += [
            "",
            "✅ *Verdict: Training passed minimum bar*",
            f"  {stats['win_rate']}% WR ≥ {MIN_SIM_GRAD_WR}% | "
            f"{stats['total']} sims ≥ {MIN_SIM_GRAD_SAMPLES}",
            "  Next: check `/readiness` — if all green, set PAPER_MODE=false for live ₹5k.",
        ]
    else:
        lines += [
            "",
            "⚠️ *Verdict: Keep training*",
            f"  Need {MIN_SIM_GRAD_WR}%+ WR and {MIN_SIM_GRAD_SAMPLES}+ sims.",
            f"  Current: {stats['win_rate']}% WR, {stats['total']} sims.",
            "  Bot will keep simming — do NOT go live yet.",
        ]

    try:
        from src.brain_metrics import assess_live_readiness
        ready_live = assess_live_readiness()
        lines += ["", f"🎯 *Full live gates:* {ready_live['reason']}"]
    except Exception:
        pass

    return '\n'.join(lines)


def maybe_send_graduation(messenger) -> bool:
    """Send phase-end reports: sim complete (week 2) and month complete (week 4)."""
    from src.shadow_learning import training_phase, init_shadow_tables
    init_shadow_tables()

    phase = training_phase()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_flags (key TEXT PRIMARY KEY, value TEXT)
    """)

    def _already(flag: str) -> bool:
        row = conn.execute(
            "SELECT value FROM bot_flags WHERE key=?", (flag,)
        ).fetchone()
        return bool(row and row[0] == 'yes')

    def _mark(flag: str):
        conn.execute(
            "INSERT OR REPLACE INTO bot_flags (key, value) VALUES (?, 'yes')",
            (flag,),
        )

    if phase == 'PAPER' and not _already('sim_graduation_sent'):
        messenger.send(format_sim_phase_complete_report())
        _mark('sim_graduation_sent')
        conn.commit()
        conn.close()
        return True

    if phase == 'LIVE_READY' and not _already('training_complete_sent'):
        messenger.send(format_graduation_report())
        _mark('training_complete_sent')
        conn.commit()
        conn.close()
        return True

    conn.close()
    return False


def reset_graduation_flag() -> dict:
    """
    Clear graduation_sent so the bot can send the graduation report again.
    Use when re-running or extending the learning phase.
    """
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_flags (key TEXT PRIMARY KEY, value TEXT)
    """)
    conn.execute("DELETE FROM bot_flags WHERE key IN ('graduation_sent', 'sim_graduation_sent', 'training_complete_sent')")
    conn.commit()
    conn.close()
    return {'ok': True, 'message': 'Graduation flag cleared — report will send when phase ends'}


def format_reset_learning_help() -> str:
    from src.sim_notify import format_quiet_mode_line
    return (
        "🔄 *Learning phase reset*\n\n"
        "Cleared `graduation_sent` flag.\n"
        "• Graduation Telegram will fire again when 14-day phase completes\n"
        "• Virtual sim data (shadow_trades) is kept — brain keeps its memory\n"
        "• To fully restart learning from zero, delete `trader_brain.db` on server "
        "(last resort only)\n\n"
        f"_{format_quiet_mode_line()}_"
    )
