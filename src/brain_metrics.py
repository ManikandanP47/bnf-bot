"""
Brain Metrics — prove the bot before real money

Paper confidence (0–100), efficiency stats, and live-readiness gates.
Salary-trader mindset: capital protection first, live only when data says so.
"""

import os
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')

MIN_PAPER_TRADES = int(os.getenv('MIN_PAPER_TRADES', '10'))
MIN_PAPER_DAYS   = int(os.getenv('MIN_PAPER_DAYS', '14'))
LEARNING_PHASE_DAYS = int(os.getenv('LEARNING_PHASE_DAYS', '14'))
MIN_WIN_RATE     = float(os.getenv('MIN_WIN_RATE', '45'))
MIN_PROFIT_FACTOR = float(os.getenv('MIN_PROFIT_FACTOR', '1.2'))
MIN_CONFIDENCE   = int(os.getenv('MIN_PAPER_CONFIDENCE', '60'))
MAX_CONSEC_LOSSES = int(os.getenv('MAX_CONSEC_LOSSES', '3'))


def _conn():
    from agents.learning_agent import BRAIN
    return BRAIN.conn


def get_closed_trades(limit: int = None) -> list:
    cols = (
        "id, date, outcome, pnl_rs, pnl_pct, score, session, hour, "
        "exit_reason, mistake_type, lesson, bias, regime"
    )
    if limit:
        rows = _conn().execute(
            f"SELECT {cols} FROM trades WHERE outcome IS NOT NULL "
            f"ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(reversed(rows))
    return _conn().execute(
        f"SELECT {cols} FROM trades WHERE outcome IS NOT NULL ORDER BY id"
    ).fetchall()


def _consecutive_losses(rows: list) -> int:
    streak = 0
    for r in reversed(rows):
        if r[2] == 'LOSS':
            streak += 1
        else:
            break
    return streak


def get_core_stats() -> dict:
    rows = get_closed_trades()
    total = len(rows)
    if total == 0:
        return {
            'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0,
            'total_pnl': 0, 'profit_factor': 0.0, 'expectancy': 0.0,
            'avg_win': 0, 'avg_loss': 0, 'consec_losses': 0,
            'trading_days': 0, 'target_exits': 0, 'sl_exits': 0,
            'efficiency_pct': 0.0, 'direction_ok_pct': 0.0,
        }

    wins   = [r for r in rows if r[2] == 'WIN']
    losses = [r for r in rows if r[2] == 'LOSS']
    gross_win  = sum(r[3] for r in wins)
    gross_loss = abs(sum(r[3] for r in losses))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0)
    avg_win  = round(gross_win / len(wins), 0) if wins else 0
    avg_loss = round(gross_loss / len(losses), 0) if losses else 0
    expectancy = round((gross_win - gross_loss) / total, 0)

    dates = {r[1] for r in rows}
    target_exits = sum(1 for r in rows if r[8] and 'TARGET' in r[8].upper())
    sl_exits     = sum(1 for r in rows if r[8] and ('SL' in r[8].upper() or 'STOP' in r[8].upper()))
    eff = round(target_exits / total * 100, 1) if total else 0

    dir_ok = 0
    for r in rows:
        lesson = (r[10] or '').lower()
        if 'direction correct' in lesson or 'valid setup' in lesson or 'perfect setup' in lesson:
            dir_ok += 1
    dir_pct = round(dir_ok / total * 100, 1) if total else 0

    return {
        'total':           total,
        'wins':            len(wins),
        'losses':          len(losses),
        'win_rate':        round(len(wins) / total * 100, 1),
        'total_pnl':       round(sum(r[3] for r in rows), 0),
        'profit_factor':   pf,
        'expectancy':      expectancy,
        'avg_win':         avg_win,
        'avg_loss':        avg_loss,
        'consec_losses':   _consecutive_losses(rows),
        'trading_days':    len(dates),
        'target_exits':    target_exits,
        'sl_exits':        sl_exits,
        'efficiency_pct':  eff,
        'direction_ok_pct': dir_pct,
    }


def compute_paper_confidence() -> dict:
    """
    0–100 score: how ready is the brain for live ₹5k?
    Transparent breakdown so you can see what still needs work.
    """
    s = get_core_stats()
    total = s['total']
    if total == 0:
        return {
            'score': 0, 'grade': 'START',
            'breakdown': [],
            'summary': 'No paper trades yet — brain is blank slate.',
            'stats': get_core_stats(),
        }

    breakdown = []
    score = 0

    # Sample size (max 25 pts)
    trade_pts = min(25, int(total / MIN_PAPER_TRADES * 25))
    breakdown.append(f"Trades {total}/{MIN_PAPER_TRADES}: +{trade_pts}/25")
    score += trade_pts

    # Calendar spread (max 15 pts) — proves consistency over weeks
    day_pts = min(15, int(s['trading_days'] / MIN_PAPER_DAYS * 15))
    breakdown.append(f"Active days {s['trading_days']}/{MIN_PAPER_DAYS}: +{day_pts}/15")
    score += day_pts

    # Win rate (max 25 pts)
    wr_pts = min(25, max(0, int((s['win_rate'] - 30) / 40 * 25)))
    breakdown.append(f"Win rate {s['win_rate']}%: +{wr_pts}/25")
    score += wr_pts

    # Profit factor (max 20 pts)
    pf = s['profit_factor']
    pf_pts = min(20, max(0, int((pf - 0.8) / 1.2 * 20))) if pf < 50 else 20
    breakdown.append(f"Profit factor {pf}: +{pf_pts}/20")
    score += pf_pts

    # Positive expectancy (max 10 pts)
    exp_pts = 10 if s['expectancy'] > 0 else 0
    breakdown.append(f"Expectancy ₹{s['expectancy']}/trade: +{exp_pts}/10")
    score += exp_pts

    # Streak penalty
    if s['consec_losses'] >= MAX_CONSEC_LOSSES:
        penalty = 15
        score = max(0, score - penalty)
        breakdown.append(f"⚠️ {s['consec_losses']} losses in a row: -{penalty}")

    score = min(100, score)
    if score >= 75:
        grade = 'STRONG'
    elif score >= 60:
        grade = 'BUILDING'
    elif score >= 40:
        grade = 'LEARNING'
    else:
        grade = 'EARLY'

    return {
        'score':     score,
        'grade':     grade,
        'breakdown': breakdown,
        'summary':   f"Paper confidence {score}/100 ({grade})",
        'stats':     s,
    }


def assess_live_readiness() -> dict:
    """All gates must pass before live ₹5k is allowed."""
    conf = compute_paper_confidence()
    s    = conf.get('stats') or get_core_stats()
    gates = []

    def gate(name, ok, detail):
        gates.append({'name': name, 'ok': ok, 'detail': detail})
        return ok

    all_ok = True
    all_ok &= gate('Paper trades', s['total'] >= MIN_PAPER_TRADES,
                   f"{s['total']}/{MIN_PAPER_TRADES} trades")
    all_ok &= gate('Active days', s['trading_days'] >= MIN_PAPER_DAYS,
                   f"{s['trading_days']}/{MIN_PAPER_DAYS} days with trades")
    all_ok &= gate('Win rate', s['win_rate'] >= MIN_WIN_RATE,
                   f"{s['win_rate']}% (need {MIN_WIN_RATE}%+)")
    all_ok &= gate('Paper P&L', s['total_pnl'] >= 0,
                   f"₹{s['total_pnl']:,} (must be positive)")
    all_ok &= gate('Profit factor', s['profit_factor'] >= MIN_PROFIT_FACTOR,
                   f"{s['profit_factor']} (need {MIN_PROFIT_FACTOR}+)")
    all_ok &= gate('Expectancy', s['expectancy'] > 0,
                   f"₹{s['expectancy']}/trade")
    all_ok &= gate('Loss streak', s['consec_losses'] < MAX_CONSEC_LOSSES,
                   f"{s['consec_losses']} consecutive (max {MAX_CONSEC_LOSSES - 1})")
    all_ok &= gate('Confidence', conf['score'] >= MIN_CONFIDENCE,
                   f"{conf['score']}/100 (need {MIN_CONFIDENCE}+)")

    try:
        from src.shadow_learning import learning_phase_info
        sh = learning_phase_info()
        if sh['in_learning_phase']:
            all_ok = False
            gate('Learning phase', False,
                 f"{sh['days_left']}d left of {LEARNING_PHASE_DAYS} — shadow drills running")
        elif sh['shadow_total'] >= 5:
            gate('Shadow drills', sh['shadow_win_rate'] >= 35,
                 f"{sh['shadow_win_rate']}% shadow WR ({sh['shadow_total']} drills)")
    except Exception:
        pass

    from src.trade_analytics import compute_drawdown, compute_r_stats
    from src.capital_guard import LIVE_CAPITAL_RS
    dd = compute_drawdown()
    r_stats = compute_r_stats()
    max_dd_allowed = LIVE_CAPITAL_RS * 0.6
    all_ok &= gate('Max drawdown', dd['max_drawdown'] < max_dd_allowed,
                   f"₹{dd['max_drawdown']:,} (max ₹{max_dd_allowed:,.0f})")
    if s['total'] >= 5:
        all_ok &= gate('+1R rate', r_stats['pct_reach_1r'] >= 25,
                       f"{r_stats['pct_reach_1r']}% trades reach +1R")

    failed = [g for g in gates if not g['ok']]
    if all_ok:
        reason = (
            f"✅ Ready for live — {s['win_rate']}% WR, "
            f"₹{s['total_pnl']:,} paper P&L, confidence {conf['score']}/100"
        )
    elif s['total'] < MIN_PAPER_TRADES:
        reason = f"Need {MIN_PAPER_TRADES}+ paper trades (have {s['total']}). Keep paper running."
    else:
        reason = f"❌ Not ready — {len(failed)} gate(s) failing. See /readiness"

    return {
        'ready':      all_ok,
        'reason':     reason,
        'confidence': conf,
        'gates':      gates,
        'win_rate':   s['win_rate'],
        'total_pnl':  s['total_pnl'],
        'trades':     s['total'],
    }


def get_dynamic_min_score(base: int = 5) -> int:
    """
    Tighten entry bar when recent paper performance is weak
    or shadow drills show poor virtual edge.
    Protects capital during learning phase.
    """
    score = base
    recent = get_closed_trades(limit=8)
    if len(recent) >= 5:
        recent_wr = sum(1 for r in recent if r[2] == 'WIN') / len(recent) * 100
        streak    = _consecutive_losses(recent)
        if recent_wr < 40:
            score = max(score, 7)
        if streak >= 2:
            score = max(score, 8)
        if streak >= MAX_CONSEC_LOSSES:
            score = max(score, 9)

    try:
        from src.shadow_tuning import shadow_score_adjustment
        sh = shadow_score_adjustment(base)
        score = max(score, sh.get('min_score', base))
    except Exception:
        pass

    return score


def check_pattern_combo(session: str, hour: int, score: int, regime: str) -> dict:
    """Block historically bad pattern combos (5+ samples)."""
    from agents.learning_agent import BRAIN
    keys = [
        f"hour:{hour}|session:{session}",
        f"score:{score}|regime:{regime}",
    ]
    for k in keys:
        wr = BRAIN.get_pattern_winrate(k, min_samples=5)
        if wr is not None and wr < 35:
            return {'block': True, 'reason': f"Pattern {k} win rate {wr:.0f}% — brain blocks"}
    return {'block': False, 'reason': ''}


def format_readiness_report() -> str:
    """Full /readiness Telegram report."""
    from src.capital_guard import LIVE_CAPITAL_RS
    from src.trade_analytics import compute_drawdown, compute_r_stats, session_expectancy
    r    = assess_live_readiness()
    from core.shared_state import STATE
    STATE.set('system.live_readiness_summary', r['reason'])
    conf = r['confidence']
    s    = conf.get('stats') or get_core_stats()
    dd   = compute_drawdown()
    r_stats = compute_r_stats()

    lines = [
        f"🎯 *Live Readiness Report*",
        f"━━━━━━━━━━━━━━━━━━━",
        f"Capital planned: ₹{LIVE_CAPITAL_RS:,.0f}",
        f"Paper confidence: *{conf['score']}/100* ({conf['grade']})",
        "",
        "*Gates (all must pass):*",
    ]
    for g in r['gates']:
        e = '✅' if g['ok'] else '❌'
        lines.append(f"  {e} {g['name']}: {g['detail']}")

    lines += [
        "",
        "*Efficiency stats:*",
        f"  Win rate: {s['win_rate']}% ({s['wins']}W / {s['losses']}L)",
        f"  Profit factor: {s['profit_factor']}",
        f"  Expectancy: ₹{s['expectancy']}/trade",
        f"  Avg win ₹{s['avg_win']:,} | Avg loss ₹{s['avg_loss']:,}",
        f"  Target exits: {s['efficiency_pct']}% of trades",
        f"  Direction correct: {s['direction_ok_pct']}%",
    ]
    lines += [
        "",
        "*R-multiple & drawdown:*",
    ]
    lines.append(f"  Avg R: {r_stats['avg_r']} | +1R rate: {r_stats['pct_reach_1r']}%")
    lines.append(f"  Max drawdown: ₹{dd['max_drawdown']:,} | Peak: ₹{dd['peak_pnl']:,}")

    sess_exp = session_expectancy()
    if sess_exp:
        lines += ["", "*Session expectancy:*"]
        for sess, v in sess_exp.items():
            lines.append(f"  {sess}: ₹{v['expectancy']}/trade ({v['trades']} trades)")

    lines += [
        "",
        "*Confidence breakdown:*",
    ]
    for b in conf['breakdown']:
        lines.append(f"  {b}")

    lines += [
        "",
        "*Salary-trader guards (active):*",
        "  Live Groww price only (no yfinance entries)",
        "  Cold start: score ≥8 until 5 paper trades",
        "  No entries after 2 PM (1 PM Wed expiry)",
        "  Max SL risk 25% of capital per trade",
        "  Expiry ≥5 days — skips expiry-week theta",
        "",
        f"*{r['reason']}*",
        "",
        "_Paper period = your backtest. Stay paper until every gate is green_ 🛡️",
    ]
    return '\n'.join(lines)


def format_confidence_line() -> str:
    """One-liner for morning brief / journal."""
    conf = compute_paper_confidence()
    s    = conf.get('stats') or get_core_stats()
    return (
        f"Paper confidence: {conf['score']}/100 ({conf['grade']}) | "
        f"{s['total']} trades | {s['win_rate']}% WR | ₹{s['total_pnl']:,}"
    )
