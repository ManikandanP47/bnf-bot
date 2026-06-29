"""
Trade Analytics — funnel, MAE/MFE, slippage, premium checks, skip-learning.
Improves paper accuracy before live ₹5k deployment.
"""

import os
import json
import sqlite3
from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
PAPER_SLIPPAGE_PER_UNIT = float(os.getenv('PAPER_SLIPPAGE_PER_UNIT', '7'))
SIM_LIVE_FILLS = os.getenv('SIM_LIVE_FILLS', 'true').lower() == 'true'
SIM_SLIPPAGE_PER_UNIT = float(os.getenv('SIM_SLIPPAGE_PER_UNIT', str(PAPER_SLIPPAGE_PER_UNIT)))
SIM_SPREAD_PCT = float(os.getenv('SIM_SPREAD_PCT', '1.0'))
MIN_PREMIUM_RS = float(os.getenv('MIN_PREMIUM_RS', '80'))
MAX_PREMIUM_RS = float(os.getenv('MAX_PREMIUM_RS', '450'))
MAX_SPREAD_PCT = float(os.getenv('MAX_SPREAD_PCT', '8'))


def _conn():
    from agents.learning_agent import BRAIN
    return BRAIN.conn


def _now():
    n = datetime.now(IST)
    return n.strftime('%Y-%m-%d'), n.strftime('%H:%M')


def log_funnel(stage: str, signal: dict = None, reason: str = ''):
    """Stage: setup_seen | risk_block | risk_ok | suggested | executed | skipped"""
    date, tm = _now()
    sig = signal or {}
    try:
        _conn().execute("""
            INSERT INTO signal_funnel (date, time, stage, score, bias, session, reason)
            VALUES (?,?,?,?,?,?,?)
        """, (
            date, tm, stage,
            sig.get('score', 0),
            sig.get('trend', sig.get('bias', '')),
            sig.get('session', ''),
            (reason or '')[:200],
        ))
    except Exception:
        pass


def log_skip(signal: dict, params: dict):
    """Record skipped setup for EOD would-have analysis."""
    date, tm = _now()
    log_funnel('skipped', signal, 'User skipped')
    try:
        _conn().execute("""
            INSERT INTO skipped_setups
            (date, time, bias, score, price, option_name, entry_prem,
             sl_prem, tgt_prem, session, signal_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date, tm,
            signal.get('trend', ''),
            signal.get('score', 0),
            signal.get('price', 0),
            params.get('name', ''),
            params.get('premium', 0),
            params.get('sl_prem', 0),
            params.get('tgt_prem', 0),
            signal.get('session', ''),
            json.dumps({'regime': signal.get('regime'), 'rsi': signal.get('rsi')}),
        ))
    except Exception:
        pass


def resolve_skipped_setups(bnf_eod: float = 0):
    """Mark skipped setups with would-have outcome using EOD price."""
    if bnf_eod <= 0:
        from core.shared_state import STATE
        bnf_eod = STATE.get('market.price', 0)
    if bnf_eod <= 0:
        return 0

    today = datetime.now(IST).strftime('%Y-%m-%d')
    rows = _conn().execute("""
        SELECT id, bias, price, entry_prem, sl_prem, tgt_prem, option_name
        FROM skipped_setups
        WHERE date=? AND resolved=0
    """, (today,)).fetchall()

    resolved = 0
    for rid, bias, entry_bnf, prem, sl, tgt, name in rows:
        from src.premium_feed import estimate_premium
        zone = {}
        strike = 0
        otype = 'CE' if bias == 'BULLISH' else 'PE'
        try:
            import re
            m = re.search(r'(\d{5})(CE|PE)', name or '')
            if m:
                strike = int(m.group(1))
                otype = m.group(2)
        except Exception:
            pass

        eod_prem = estimate_premium(prem, entry_bnf, bnf_eod, strike, otype)
        qty = 15
        if eod_prem <= sl:
            outcome, pnl = 'WOULD_LOSS', round((sl - prem) * qty, 0)
            note = 'Would have hit SL'
        elif eod_prem >= tgt:
            outcome, pnl = 'WOULD_WIN', round((tgt - prem) * qty, 0)
            note = 'Would have hit target'
        elif eod_prem > prem:
            outcome, pnl = 'WOULD_WIN', round((eod_prem - prem) * qty, 0)
            note = 'Would have been green at EOD'
        else:
            outcome, pnl = 'WOULD_LOSS', round((eod_prem - prem) * qty, 0)
            note = 'Would have been red at EOD'

        _conn().execute("""
            UPDATE skipped_setups SET resolved=1, would_outcome=?, would_pnl_rs=?, notes=?
            WHERE id=?
        """, (outcome, pnl, note, rid))
        resolved += 1
    return resolved


def get_funnel_summary(days: int = 1) -> dict:
    since = (datetime.now(IST) - __import__('datetime').timedelta(days=days - 1)).strftime('%Y-%m-%d')
    rows = _conn().execute(
        "SELECT stage, COUNT(*) FROM signal_funnel WHERE date>=? GROUP BY stage",
        (since,),
    ).fetchall()
    counts = {r[0]: r[1] for r in rows}
    seen     = counts.get('setup_seen', 0)
    blocked  = counts.get('risk_block', 0)
    ok       = counts.get('risk_ok', 0)
    suggested= counts.get('suggested', 0)
    executed = counts.get('executed', 0)
    skipped  = counts.get('skipped', 0)
    conv = round(executed / seen * 100, 1) if seen else 0
    return {
        'seen': seen, 'risk_block': blocked, 'risk_ok': ok,
        'suggested': suggested, 'executed': executed, 'skipped': skipped,
        'conversion_pct': conv,
    }


def get_skip_stats() -> dict:
    rows = _conn().execute("""
        SELECT would_outcome, COUNT(*), COALESCE(SUM(would_pnl_rs),0)
        FROM skipped_setups WHERE resolved=1 GROUP BY would_outcome
    """).fetchall()
    stats = {r[0]: {'count': r[1], 'pnl': r[2]} for r in rows}
    total = sum(v['count'] for v in stats.values())
    would_win = stats.get('WOULD_WIN', {}).get('count', 0)
    return {
        'total_resolved': total,
        'would_win': would_win,
        'would_loss': stats.get('WOULD_LOSS', {}).get('count', 0),
        'skip_accuracy': round(would_win / total * 100, 1) if total else 0,
        'missed_profit': round(stats.get('WOULD_WIN', {}).get('pnl', 0), 0),
    }


def update_mae_mfe(learning_id: int, unrealized_rs: float):
    """Track max adverse / favorable excursion during open trade."""
    if not learning_id:
        return
    try:
        row = _conn().execute(
            "SELECT mae_rs, mfe_rs FROM trades WHERE id=?", (learning_id,)
        ).fetchone()
        mae = row[0] if row and row[0] is not None else 0
        mfe = row[1] if row and row[1] is not None else 0
        new_mae = min(mae, unrealized_rs) if mae != 0 else min(0, unrealized_rs)
        new_mfe = max(mfe, unrealized_rs) if mfe != 0 else max(0, unrealized_rs)
        if unrealized_rs < 0:
            new_mae = min(mae if mae else 0, unrealized_rs)
        if unrealized_rs > 0:
            new_mfe = max(mfe if mfe else 0, unrealized_rs)
        _conn().execute(
            "UPDATE trades SET mae_rs=?, mfe_rs=? WHERE id=?",
            (round(new_mae, 0), round(new_mfe, 0), learning_id),
        )
    except Exception:
        pass


def apply_paper_slippage(pnl_rs: float, qty: int = 15) -> tuple:
    """Round-trip slippage on entry+exit for realistic paper P&L."""
    slip = round(PAPER_SLIPPAGE_PER_UNIT * qty * 2, 0)
    return round(pnl_rs - slip, 0), slip


def virtual_buy_fill_price(ltp: float) -> dict:
    """
    Live-like BUY fill from WebSocket/REST LTP.
    Market buy pays above LTP: spread + per-unit slippage.
    """
    if ltp <= 0:
        return {'fill': 0.0, 'ltp': 0.0, 'friction': 0.0, 'live_like': False}
    if not SIM_LIVE_FILLS:
        return {'fill': round(ltp, 1), 'ltp': ltp, 'friction': 0.0, 'live_like': False}
    spread = ltp * SIM_SPREAD_PCT / 100
    friction = spread + SIM_SLIPPAGE_PER_UNIT
    fill = round(ltp + friction, 1)
    return {'fill': fill, 'ltp': ltp, 'friction': round(friction, 2), 'live_like': True}


def virtual_sell_fill_price(ltp: float) -> dict:
    """
    Live-like SELL fill from WebSocket/REST LTP.
    Market sell receives below LTP: spread + per-unit slippage.
    """
    if ltp <= 0:
        return {'fill': 0.0, 'ltp': 0.0, 'friction': 0.0, 'live_like': False}
    if not SIM_LIVE_FILLS:
        return {'fill': round(ltp, 1), 'ltp': ltp, 'friction': 0.0, 'live_like': False}
    spread = ltp * SIM_SPREAD_PCT / 100
    friction = spread + SIM_SLIPPAGE_PER_UNIT
    fill = round(max(ltp - friction, 1.0), 1)
    return {'fill': fill, 'ltp': ltp, 'friction': round(friction, 2), 'live_like': True}


def virtual_live_pnl(entry_fill: float, ltp_now: float, qty: int = 15) -> dict:
    """P&L using buy-fill at entry and sell-fill at current LTP."""
    sell = virtual_sell_fill_price(ltp_now)
    pnl = round((sell['fill'] - entry_fill) * qty, 0) if entry_fill else 0
    return {
        'pnl_rs': pnl,
        'ltp': ltp_now,
        'sell_fill': sell['fill'],
        'live_like': sell['live_like'],
    }


def compute_drawdown() -> dict:
    """Peak-to-trough paper P&L and longest losing day streak."""
    rows = _conn().execute("""
        SELECT date, COALESCE(SUM(pnl_rs),0) as day_pnl
        FROM trades WHERE outcome IS NOT NULL
          AND (mode IS NULL OR mode = 'paper')
        GROUP BY date ORDER BY date
    """).fetchall()
    if not rows:
        return {'max_drawdown': 0, 'peak_pnl': 0, 'current_pnl': 0, 'losing_day_streak': 0}

    cumulative = 0
    peak = 0
    max_dd = 0
    losing_streak = 0
    max_losing_streak = 0
    for _, day_pnl in rows:
        cumulative += day_pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        if day_pnl < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    return {
        'max_drawdown': round(max_dd, 0),
        'peak_pnl':     round(peak, 0),
        'current_pnl':  round(cumulative, 0),
        'losing_day_streak': max_losing_streak,
    }


def compute_r_stats() -> dict:
    rows = _conn().execute("""
        SELECT r_multiple, outcome FROM trades
        WHERE outcome IS NOT NULL AND r_multiple IS NOT NULL
          AND (mode IS NULL OR mode = 'paper')
    """).fetchall()
    if not rows:
        return {'avg_r': 0, 'pct_reach_1r': 0, 'avg_r_wins': 0, 'avg_r_losses': 0}

    all_r = [r[0] for r in rows]
    wins  = [r[0] for r in rows if r[1] == 'WIN']
    losses= [r[0] for r in rows if r[1] == 'LOSS']
    reach_1r = sum(1 for r in all_r if r >= 1.0)
    return {
        'avg_r':         round(sum(all_r) / len(all_r), 2),
        'pct_reach_1r':  round(reach_1r / len(all_r) * 100, 1),
        'avg_r_wins':    round(sum(wins) / len(wins), 2) if wins else 0,
        'avg_r_losses':  round(sum(losses) / len(losses), 2) if losses else 0,
    }


def breakeven_win_rate(avg_win: float, avg_loss: float) -> float:
    if avg_win <= 0:
        return 100.0
    if avg_loss <= 0:
        return 0.0
    return round(avg_loss / (avg_win + avg_loss) * 100, 1)


def session_expectancy() -> dict:
    rows = _conn().execute("""
        SELECT session, COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
               COALESCE(SUM(pnl_rs),0)
        FROM trades WHERE outcome IS NOT NULL AND session IS NOT NULL
        GROUP BY session
    """).fetchall()
    result = {}
    for sess, n, wins, pnl in rows:
        result[sess] = {
            'trades': n,
            'win_rate': round(wins / n * 100, 1) if n else 0,
            'expectancy': round(pnl / n, 0) if n else 0,
            'total_pnl': round(pnl, 0),
        }
    return result


def check_premium_sanity(premium: float, lot_cost: float) -> dict:
    from src.capital_guard import LIVE_CAPITAL_RS
    if premium < MIN_PREMIUM_RS:
        return {
            'ok': False,
            'reason': f'Premium ₹{premium:.0f} too low — lottery ticket, skip',
        }
    if premium > MAX_PREMIUM_RS:
        return {
            'ok': False,
            'reason': f'Premium ₹{premium:.0f} too high for ₹{LIVE_CAPITAL_RS:,.0f} capital',
        }
    if lot_cost > LIVE_CAPITAL_RS * 0.85:
        return {
            'ok': False,
            'reason': f'Lot cost ₹{lot_cost:,.0f} eats too much capital',
        }
    return {'ok': True, 'reason': f'Premium ₹{premium:.0f} in sane range ✅'}


def check_liquidity(strike: int, opt_type: str, expiry: str, premium: float) -> dict:
    """Estimate spread risk — block if premium too thin for reliable fills."""
    if premium < 100:
        return {
            'ok': False,
            'reason': f'Thin premium ₹{premium:.0f} — wide spread risk on fills',
        }
    token = ''
    try:
        from core.shared_state import STATE
        token = STATE.get('system.groww_token', '')
    except Exception:
        pass
    if token and strike and expiry:
        try:
            ltp = __import__('src.premium_feed', fromlist=['fetch_option_ltp']).fetch_option_ltp(
                strike, opt_type, expiry
            )
            if ltp > 0 and abs(ltp - premium) / premium * 100 > MAX_SPREAD_PCT:
                return {
                    'ok': False,
                    'reason': f'Premium moved {abs(ltp-premium)/premium*100:.0f}% — liquidity unstable',
                }
        except Exception:
            pass
    return {'ok': True, 'reason': 'Liquidity OK ✅'}


def detect_theta_loss(hold_min: int, session: str, pnl_rs: float,
                      mfe_rs: float, exit_reason: str) -> bool:
    """Afternoon hold with positive MFE but EOD loss = theta decay."""
    if pnl_rs >= 0:
        return False
    if hold_min < 90:
        return False
    if 'EOD' not in (exit_reason or '').upper():
        return False
    if mfe_rs and mfe_rs > abs(pnl_rs) * 0.5:
        return True
    now = datetime.now(IST).time()
    return now >= dtime(14, 0) or 'AFTERNOON' in (session or '').upper()


def apply_mistake_auto_rules() -> dict:
    """
    After enough trades, auto-adjust SL width or block regimes.
    Returns dict applied to STATE['brain'].
    """
    rows = _conn().execute("""
        SELECT mistake_type FROM trades
        WHERE outcome='LOSS' AND mistake_type IS NOT NULL
        ORDER BY id DESC LIMIT 20
    """).fetchall()
    if len(rows) < 8:
        return {'sl_widen_pct': 0, 'block_ranging': False, 'min_score_boost': 0}

    from collections import Counter
    counts = Counter(r[0] for r in rows)
    top, top_n = counts.most_common(1)[0]
    rules = {'sl_widen_pct': 0, 'block_ranging': False, 'min_score_boost': 0, 'note': ''}

    if top in ('MARKET_MOVE', 'SL_TIGHT') and top_n >= 4:
        rules['sl_widen_pct'] = 0.10
        rules['note'] = 'SL widened 10% — many valid setups stopped'
    if top == 'RANGING_MARKET' and top_n >= 3:
        rules['block_ranging'] = True
        rules['note'] = 'RANGING entries blocked — pattern failing'
    if top == 'LOW_SCORE' and top_n >= 3:
        rules['min_score_boost'] = 1
        rules['note'] = 'Min score +1 — low-score losses clustering'
    if top == 'TIMING' and top_n >= 3:
        rules['note'] = 'Theta/timing losses — avoid late entries'

    return rules


def format_funnel_report() -> str:
    f = get_funnel_summary()
    sk = get_skip_stats()
    lines = [
        "📊 *Signal Funnel (today)*",
        "━━━━━━━━━━━━━━━━━━━",
        f"  Setups seen:    {f['seen']}",
        f"  Risk blocked:   {f['risk_block']}",
        f"  Risk passed:    {f['risk_ok']}",
        f"  Suggested:      {f['suggested']}",
        f"  You executed:   {f['executed']}",
        f"  You skipped:    {f['skipped']}",
        f"  Conversion:     {f['conversion_pct']}%",
    ]
    if sk['total_resolved']:
        lines += [
            "",
            "*Skip learning (all time):*",
            f"  Skips resolved: {sk['total_resolved']}",
            f"  Good skips: {100 - sk['skip_accuracy']:.0f}% (avoided losses)",
            f"  Missed wins: {sk['would_win']} (₹{sk['missed_profit']:,} left on table)",
        ]
    return '\n'.join(lines)


def format_breakeven_line(params: dict) -> str:
    entry = params.get('premium', 0)
    sl    = params.get('sl_prem', 0)
    tgt   = params.get('tgt_prem', 0)
    qty   = params.get('qty', 15)
    avg_win  = (tgt - entry) * qty * 0.6
    avg_loss = (entry - sl) * qty
    if avg_win <= 0 or avg_loss <= 0:
        return ''
    be = breakeven_win_rate(avg_win, avg_loss)
    return f"📐 Break-even: need *{be}%* wins (avg win ₹{avg_win:,.0f} / loss ₹{avg_loss:,.0f})"
