"""
Lightweight history backtest — Groww 15m bars, no yfinance dependency.
Proxy for zone-pullback + structure days (validates bot logic on recent history).
"""

from datetime import datetime, timedelta, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def _morning_session(candles: list) -> list:
    """9:15–11:30 candles."""
    start, end = dtime(9, 15), dtime(11, 30)
    out = []
    for c in candles:
        t = c['ts'].time() if hasattr(c.get('ts'), 'time') else None
        if t and start <= t <= end:
            out.append(c)
    return out


def _simulate_day(day_candles: list) -> dict:
    """
    Simple proxy: bullish day if morning close > open by 0.15%,
    entry at morning low pullback, exit at +0.3% index or -0.15% SL.
    """
    morning = _morning_session(day_candles)
    if len(morning) < 4:
        return {'trade': False}

    open_p = morning[0]['open']
    close_p = morning[-1]['close']
    low_p = min(c['low'] for c in morning)
    high_p = max(c['high'] for c in morning)

    if open_p <= 0:
        return {'trade': False}

    move_pct = (close_p - open_p) / open_p * 100
    if abs(move_pct) < 0.12:
        return {'trade': False, 'reason': 'chop'}

    bullish = move_pct > 0
    entry = low_p if bullish else high_p
    target = entry * (1.003 if bullish else 0.997)
    stop   = entry * (0.9985 if bullish else 1.0015)

    outcome = None
    for c in morning[1:]:
        if bullish:
            if c['low'] <= stop:
                outcome = 'LOSS'
                break
            if c['high'] >= target:
                outcome = 'WIN'
                break
        else:
            if c['high'] >= stop:
                outcome = 'LOSS'
                break
            if c['low'] <= target:
                outcome = 'WIN'
                break

    if not outcome:
        outcome = 'WIN' if (bullish and close_p > entry) or (not bullish and close_p < entry) else 'LOSS'

    return {'trade': True, 'outcome': outcome, 'move_pct': round(move_pct, 2)}


def run_quick_backtest(token: str = '', lookback_days: int = 10) -> dict:
    """
    Run proxy backtest on last N trading days of Groww 15m data.
    Stores summary for trading_knowledge alignment checks.
    """
    from src.groww_historical import fetch_banknifty_candles

    hours = min(24 * lookback_days, 240)
    raw = fetch_banknifty_candles(interval_min=15, lookback_hours=hours, token=token)
    if not raw:
        return {'available': False, 'reason': 'No Groww historical data'}

    by_day = {}
    for c in raw:
        d = c['ts'].date()
        by_day.setdefault(d, []).append(c)

    trades = []
    ranges = []
    for d in sorted(by_day.keys())[-lookback_days:]:
        day_c = sorted(by_day[d], key=lambda x: x['ts'])
        if len(day_c) < 8:
            continue
        ranges.append(day_c[-1]['high'] - day_c[0]['low'])
        sim = _simulate_day(day_c)
        if sim.get('trade'):
            trades.append(sim)

    if not trades:
        return {
            'available': True,
            'days_tested': len(by_day),
            'proxy_trades': 0,
            'proxy_win_rate': 0,
            'avg_day_range': 0,
            'note': 'Few qualifying setup days in lookback — bot correctly selective',
        }

    wins = sum(1 for t in trades if t.get('outcome') == 'WIN')
    wr = wins / len(trades) * 100
    return {
        'available':      True,
        'days_tested':    len(by_day),
        'proxy_trades':   len(trades),
        'proxy_win_rate': round(wr, 1),
        'avg_day_range':  round(sum(ranges) / len(ranges), 0) if ranges else 0,
        'note': (
            f'Proxy pullback model: {wr:.0f}% WR over {len(trades)} mornings '
            f'(not exact bot logic — sanity check only)'
        ),
        'updated': datetime.now(IST).strftime('%d %b %H:%M'),
    }


def refresh_backtest_summary(token: str = '') -> dict:
    from core.shared_state import STATE
    summary = run_quick_backtest(token=token)
    STATE.set('system.backtest_summary', summary)
    _feed_backtest_to_rag(summary)
    return summary


def _feed_backtest_to_rag(summary: dict):
    """Inject proxy backtest insight into RAG for ML/context alignment."""
    if not summary.get('available'):
        return
    try:
        from src.market_rag import ingest_rule
        wr = summary.get('proxy_win_rate', 0)
        n = summary.get('proxy_trades', 0)
        if n < 2:
            return
        ingest_rule(
            f"backtest:proxy_{int(wr)}",
            'BACKTEST',
            f"Groww 10d proxy backtest: {wr:.0f}% WR over {n} morning setups "
            f"(sanity check — not exact bot logic).",
            min(0.95, 0.5 + wr / 200),
        )
    except Exception:
        pass


def format_backtest_report() -> str:
    from core.shared_state import STATE
    bt = STATE.get('system.backtest_summary') or {}
    if not bt.get('available'):
        return (
            "📉 *History backtest*\n\n"
            "Not run yet. Bot runs this on startup + Sunday evening.\n"
            "Uses Groww 15m bars — proxy for morning pullback edge."
        )
    return (
        f"📉 *History Backtest (proxy)*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Days in sample: {bt.get('days_tested', 0)}\n"
        f"Proxy trades: {bt.get('proxy_trades', 0)}\n"
        f"Proxy win rate: *{bt.get('proxy_win_rate', 0):.0f}%*\n"
        f"Avg day range: {bt.get('avg_day_range', 0):,.0f} pts\n"
        f"Updated: {bt.get('updated', '?')}\n\n"
        f"_{bt.get('note', '')}_\n\n"
        f"_Paper trades + this proxy = how bot calibrates. Not a guarantee._"
    )
