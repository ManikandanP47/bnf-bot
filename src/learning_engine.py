"""
Learning Engine — Bot learns from every trade
Records patterns → analyses what works → improves decisions
Gets smarter every week automatically
"""

import json
import os
from datetime import datetime
from collections import defaultdict
import pytz

IST          = pytz.timezone('Asia/Kolkata')
BRAIN_FILE   = 'bot_brain.json'
JOURNAL_FILE = 'journal.json'


# ─── DEFAULT THRESHOLDS (starting point) ─────────────────────────
DEFAULT_BRAIN = {
    'version':          1,
    'total_trades':     0,
    'total_wins':       0,
    'total_losses':     0,
    'win_rate':         0.0,

    # Adaptive thresholds — bot adjusts these over time
    'min_score':        5,      # Starts at 5, increases as bot learns
    'max_trades_per_day': 1,    # Starts at 1, increases with experience
    'min_rr':           2.0,    # Minimum R:R to enter

    # Pattern memory — what the bot has learned
    'patterns': {
        'best_hours':    {},    # Hour → win rate
        'best_days':     {},    # Monday-Friday → win rate
        'score_accuracy':{},    # Score level → win rate
        'bnf_levels':    {},    # BNF range → win rate
        'monthly_pnl':   {}     # Month → net P&L
    },

    'last_updated': '',
    'insights':     []          # Human-readable learned insights
}


def load_brain() -> dict:
    try:
        if os.path.exists(BRAIN_FILE):
            with open(BRAIN_FILE) as f:
                return json.load(f)
    except:
        pass
    return DEFAULT_BRAIN.copy()


def save_brain(brain: dict):
    brain['last_updated'] = datetime.now(IST).strftime('%d %b %Y %I:%M %p')
    with open(BRAIN_FILE, 'w') as f:
        json.dump(brain, f, indent=2, default=str)


def load_journal() -> list:
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE) as f:
                return json.load(f)
    except:
        pass
    return []


def record_trade_entry(trade: dict, score: int,
                       bnf_level: float, hour: int, day: str):
    """
    Record trade ENTRY with full context for learning.
    Called when bot enters a trade.
    """
    journal = load_journal()
    entry   = {
        'id':        len(journal) + 1,
        'name':      trade.get('name'),
        'entry_time': datetime.now(IST).strftime('%d %b %Y %I:%M %p'),
        'day':        day,
        'hour':       hour,
        'bnf_level':  round(bnf_level, 0),
        'bnf_range':  get_bnf_range(bnf_level),
        'score':      score,
        'entry_prem': trade.get('entry_premium'),
        'sl_prem':    trade.get('sl_prem'),
        'tgt_prem':   trade.get('tgt_prem'),
        'status':     'OPEN',
        'pnl_rs':     0,
        'pnl_pct':    0
    }
    journal.append(entry)
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(journal, f, indent=2, default=str)
    return entry['id']


def record_trade_exit(trade_id: int, pnl_rs: float,
                      pnl_pct: float, exit_reason: str):
    """
    Record trade EXIT with P&L.
    Called when bot exits — this is how bot learns win/loss.
    """
    journal = load_journal()
    for trade in journal:
        if trade.get('id') == trade_id:
            trade['exit_time']   = datetime.now(IST).strftime('%d %b %Y %I:%M %p')
            trade['pnl_rs']      = pnl_rs
            trade['pnl_pct']     = pnl_pct
            trade['exit_reason'] = exit_reason
            trade['status']      = 'WIN' if pnl_rs > 0 else 'LOSS'
            break

    with open(JOURNAL_FILE, 'w') as f:
        json.dump(journal, f, indent=2, default=str)

    # Trigger learning after every trade
    learn_from_journal()


def get_bnf_range(level: float) -> str:
    """Categorise BankNifty level for learning"""
    if level < 48000:  return 'BELOW_48K'
    if level < 50000:  return '48K-50K'
    if level < 52000:  return '50K-52K'
    if level < 54000:  return '52K-54K'
    if level < 56000:  return '54K-56K'
    if level < 58000:  return '56K-58K'
    if level < 60000:  return '58K-60K'
    return 'ABOVE_60K'


def learn_from_journal():
    """
    Core learning function.
    Analyses all closed trades and updates the brain.
    Called after every trade exit.
    """
    journal = load_journal()
    closed  = [t for t in journal if t.get('status') in ('WIN','LOSS')]

    if len(closed) < 3:
        print(f"   Learning: {len(closed)} trades — need 3+ to learn")
        return

    brain = load_brain()
    brain['total_trades'] = len(closed)
    brain['total_wins']   = sum(1 for t in closed if t['status']=='WIN')
    brain['total_losses'] = sum(1 for t in closed if t['status']=='LOSS')
    brain['win_rate']     = round(brain['total_wins']/brain['total_trades']*100, 1)

    # ── Learn by hour ─────────────────────────────────────────────
    hour_stats = defaultdict(lambda: {'wins':0,'total':0})
    for t in closed:
        h = t.get('hour', 10)
        hour_stats[h]['total'] += 1
        if t['status'] == 'WIN':
            hour_stats[h]['wins'] += 1

    brain['patterns']['best_hours'] = {
        str(h): round(v['wins']/v['total']*100, 1)
        for h, v in hour_stats.items() if v['total'] >= 2
    }

    # ── Learn by day ──────────────────────────────────────────────
    day_stats = defaultdict(lambda: {'wins':0,'total':0})
    for t in closed:
        d = t.get('day', 'Mon')
        day_stats[d]['total'] += 1
        if t['status'] == 'WIN':
            day_stats[d]['wins'] += 1

    brain['patterns']['best_days'] = {
        d: round(v['wins']/v['total']*100, 1)
        for d, v in day_stats.items() if v['total'] >= 2
    }

    # ── Learn by score ────────────────────────────────────────────
    score_stats = defaultdict(lambda: {'wins':0,'total':0})
    for t in closed:
        s = t.get('score', 5)
        score_stats[s]['total'] += 1
        if t['status'] == 'WIN':
            score_stats[s]['wins'] += 1

    brain['patterns']['score_accuracy'] = {
        str(s): round(v['wins']/v['total']*100, 1)
        for s, v in score_stats.items() if v['total'] >= 2
    }

    # ── Learn by BankNifty range ──────────────────────────────────
    bnf_stats = defaultdict(lambda: {'wins':0,'total':0})
    for t in closed:
        r = t.get('bnf_range', 'UNKNOWN')
        bnf_stats[r]['total'] += 1
        if t['status'] == 'WIN':
            bnf_stats[r]['wins'] += 1

    brain['patterns']['bnf_levels'] = {
        r: round(v['wins']/v['total']*100, 1)
        for r, v in bnf_stats.items() if v['total'] >= 2
    }

    # ── Monthly P&L tracking ──────────────────────────────────────
    month_pnl = defaultdict(float)
    for t in closed:
        month = t.get('entry_time','')[:8]  # 'dd Mon Y'
        month_pnl[month] += t.get('pnl_rs', 0)
    brain['patterns']['monthly_pnl'] = dict(month_pnl)

    # ── ADAPTIVE THRESHOLD UPDATES ────────────────────────────────
    insights = []

    # 1. Adjust min score based on what actually works
    if len(closed) >= 10:
        # Find lowest score with 60%+ win rate
        score_wr = brain['patterns']['score_accuracy']
        profitable_scores = [
            int(s) for s, wr in score_wr.items()
            if wr >= 60
        ]
        if profitable_scores:
            new_min = min(profitable_scores)
            if new_min != brain['min_score']:
                old = brain['min_score']
                brain['min_score'] = new_min
                insights.append(
                    f"📊 Min score adjusted {old}→{new_min} "
                    f"(score {new_min} wins {score_wr.get(str(new_min),0):.0f}%)"
                )

    # 2. Increase max trades/day after consistent profitability
    if len(closed) >= 20:
        recent_20    = closed[-20:]
        recent_wins  = sum(1 for t in recent_20 if t['status']=='WIN')
        recent_wr    = recent_wins / 20 * 100
        if recent_wr >= 60 and brain['max_trades_per_day'] < 2:
            brain['max_trades_per_day'] = 2
            insights.append(
                f"📈 Max trades/day increased to 2 "
                f"(last 20 win rate: {recent_wr:.0f}%)"
            )
        elif recent_wr >= 70 and brain['max_trades_per_day'] < 3:
            brain['max_trades_per_day'] = 3
            insights.append(
                f"🚀 Max trades/day increased to 3 "
                f"(last 20 win rate: {recent_wr:.0f}%)"
            )

    # 3. Learn best entry hours
    best_hours = brain['patterns']['best_hours']
    if best_hours:
        best_h = max(best_hours, key=best_hours.get)
        best_wr = best_hours[best_h]
        if best_wr >= 65:
            insights.append(
                f"⏰ Best entry hour: {best_h}:00-{int(best_h)+1}:00 "
                f"({best_wr:.0f}% win rate)"
            )

    # 4. Learn best days
    best_days = brain['patterns']['best_days']
    if best_days:
        best_day = max(best_days, key=best_days.get)
        best_dwr = best_days[best_day]
        if best_dwr >= 65:
            insights.append(
                f"📅 Best trading day: {best_day} "
                f"({best_dwr:.0f}% win rate)"
            )

    # 5. Warn about bad conditions
    bad_days = {d:wr for d,wr in best_days.items() if wr < 35}
    for day, wr in bad_days.items():
        insights.append(f"⚠️ Avoid {day} — only {wr:.0f}% win rate")

    brain['insights'] = insights[-10:]  # Keep last 10 insights

    save_brain(brain)
    print(f"   🧠 Bot learned: {brain['total_trades']} trades, "
          f"{brain['win_rate']:.0f}% win rate")
    return brain


def get_adjusted_thresholds() -> dict:
    """
    Returns current adaptive thresholds.
    Bot uses these instead of hardcoded values.
    """
    brain = load_brain()
    return {
        'min_score':          brain.get('min_score', 5),
        'max_trades_per_day': brain.get('max_trades_per_day', 1),
        'min_rr':             brain.get('min_rr', 2.0),
        'win_rate':           brain.get('win_rate', 0.0),
        'total_trades':       brain.get('total_trades', 0)
    }


def should_trade_now(hour: int, day: str,
                     bnf_level: float, score: int) -> dict:
    """
    Bot checks its brain before entering.
    Returns: {'proceed': True/False, 'reason': str, 'confidence': int}
    """
    brain     = load_brain()
    thresholds = get_adjusted_thresholds()
    reasons   = []
    warnings  = []

    # Score check (adaptive)
    min_score = thresholds['min_score']
    if score < min_score:
        return {
            'proceed':    False,
            'reason':     f"Score {score} below learned threshold {min_score}",
            'confidence': 0
        }
    reasons.append(f"Score {score} ≥ {min_score} ✅")

    # Check if this hour is known bad
    best_hours = brain['patterns'].get('best_hours', {})
    hour_wr    = best_hours.get(str(hour), None)
    if hour_wr is not None:
        if hour_wr < 35:
            warnings.append(f"⚠️ Hour {hour}:00 historically {hour_wr:.0f}% win rate")
        elif hour_wr >= 60:
            reasons.append(f"⏰ Hour {hour}:00 win rate {hour_wr:.0f}% ✅")

    # Check if this day is known bad
    best_days = brain['patterns'].get('best_days', {})
    day_wr    = best_days.get(day, None)
    if day_wr is not None:
        if day_wr < 35:
            warnings.append(f"⚠️ {day} historically {day_wr:.0f}% win rate")
        elif day_wr >= 60:
            reasons.append(f"📅 {day} win rate {day_wr:.0f}% ✅")

    # BankNifty range check
    bnf_range = get_bnf_range(bnf_level)
    bnf_wrs   = brain['patterns'].get('bnf_levels', {})
    bnf_wr    = bnf_wrs.get(bnf_range, None)
    if bnf_wr is not None:
        if bnf_wr < 35:
            warnings.append(f"⚠️ BNF in {bnf_range} zone: {bnf_wr:.0f}% win rate")
        elif bnf_wr >= 60:
            reasons.append(f"📍 {bnf_range} win rate {bnf_wr:.0f}% ✅")

    # If too many warnings → skip
    if len(warnings) >= 2:
        return {
            'proceed':    False,
            'reason':     f"Multiple risk factors: {' | '.join(warnings)}",
            'confidence': 20
        }

    confidence = min(50 + score*5 + len(reasons)*5, 95)

    return {
        'proceed':    True,
        'reasons':    reasons,
        'warnings':   warnings,
        'confidence': confidence,
        'thresholds': thresholds
    }


def weekly_learning_report() -> str:
    """
    Weekly Telegram report — what bot learned this week.
    Sent every Sunday 8 PM.
    """
    brain   = load_brain()
    journal = load_journal()

    week_trades = [t for t in journal if t.get('status') in ('WIN','LOSS')][-10:]
    week_wins   = sum(1 for t in week_trades if t['status']=='WIN')
    week_pnl    = sum(t.get('pnl_rs', 0) for t in week_trades)

    insights_text = '\n'.join(
        f"  {x}" for x in brain.get('insights', ['Still learning...'])
    )

    best_hours = brain['patterns'].get('best_hours', {})
    best_h     = max(best_hours, key=best_hours.get) if best_hours else 'N/A'
    best_days  = brain['patterns'].get('best_days', {})
    best_d     = max(best_days, key=best_days.get) if best_days else 'N/A'

    return (
        f"🧠 *Weekly Learning Report*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📊 *Overall Stats:*\n"
        f"  Total trades: {brain['total_trades']}\n"
        f"  Win rate: {brain['win_rate']:.1f}%\n"
        f"  Wins: {brain['total_wins']} | Losses: {brain['total_losses']}\n\n"

        f"📅 *This Week:*\n"
        f"  Trades: {len(week_trades)}\n"
        f"  Wins: {week_wins}/{len(week_trades)}\n"
        f"  P&L: ₹{week_pnl:,.0f}\n\n"

        f"🎯 *Adaptive Settings:*\n"
        f"  Min score: {brain['min_score']} (was 5)\n"
        f"  Max trades/day: {brain['max_trades_per_day']}\n\n"

        f"💡 *What I Learned:*\n{insights_text}\n\n"

        f"⏰ Best hour: {best_h}:00\n"
        f"📅 Best day: {best_d}\n\n"

        f"_Bot getting smarter every trade_ 🤖"
    )


if __name__ == '__main__':
    # Demo: simulate learning from a few trades
    print("Learning engine test:")

    # Simulate 5 trades
    trades = [
        (5, 58000, 10, 'Tuesday',  1500, True),
        (7, 57500, 11, 'Wednesday',-1192, False),
        (9, 58200, 10, 'Tuesday',  3975, True),
        (6, 57800, 14, 'Thursday', -1192, False),
        (8, 58100, 10, 'Tuesday',  2800, True),
    ]

    for i, (score, bnf, hour, day, pnl, win) in enumerate(trades, 1):
        tid = record_trade_entry(
            {'name': f'BANKNIFTY 58300 CE', 'entry_premium': 265,
             'sl_prem': 186, 'tgt_prem': 530},
            score, bnf, hour, day
        )
        record_trade_exit(tid, pnl, pnl/265/15*100,
                         'target' if win else 'sl')

    brain = load_brain()
    print(f"Win rate: {brain['win_rate']}%")
    print(f"Min score adjusted to: {brain['min_score']}")
    print(f"Insights: {brain['insights']}")
    print()
    print(weekly_learning_report())
