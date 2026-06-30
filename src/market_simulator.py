"""
Market Simulator — play with live BNF flow in memory only.

During the 2-week learning phase the bot:
  • Picks CE/PE (e.g. 58300 CE), assumes premium from live LTP or model
  • Watches real price + premium path until SL, target, flow fade, or EOD
  • Logs MAE/MFE, lessons, pattern_memory — no Groww orders, no real money

This is separate from the strict Execute path (15+ filters). Simulation uses
relaxed "pro trader feel" rules: flow, session, momentum, zone proximity.
"""

import os
import sqlite3
from datetime import datetime, time as dtime, timedelta
import pytz

from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
LOT_SIZE = 15

SIM_ENABLED = os.getenv('MARKET_SIM', 'true').lower() == 'true'
SIM_MAX_PER_DAY = int(os.getenv('SIM_MAX_PER_DAY', '15'))
SIM_MAX_OPEN = int(os.getenv('SIM_MAX_OPEN', '2'))
SIM_SCAN_MINUTES = int(os.getenv('SIM_SCAN_MINUTES', '4'))
SIM_MIN_SCORE = int(os.getenv('SIM_MIN_SCORE', '5'))
SIM_MIN_GAP_MIN = int(os.getenv('SIM_MIN_GAP_MIN', '8'))
SIM_SKIP_CHOP_SESSIONS = os.getenv('SIM_SKIP_CHOP_SESSIONS', 'true').lower() == 'true'
SIM_ALIGN_EXECUTE = os.getenv('SIM_ALIGN_EXECUTE', 'true').lower() == 'true'

from src.sim_realism import SIM_MIN_DAYS_TO_EXPIRY, check_sim_entry_gates

_CHOP_SESSIONS = frozenset({
    'LUNCH_CHOP', 'EOD_CHOP', 'OPEN_VOLATILE', 'OPENING_CHAOS',
    'PRE_MARKET', 'CLOSED',
})

_last_scan_at = None
_last_open_at = None


def _conn():
    return sqlite3.connect(DB_FILE)


def _notify(msg: str, kind: str = 'open', pnl_rs: float = 0, outcome: str = ''):
    try:
        from src.sim_notify import notify_sim_telegram
        notify_sim_telegram(msg, kind=kind, pnl_rs=pnl_rs, outcome=outcome)
    except Exception:
        try:
            from core.messenger import Messenger
            Messenger().send(msg)
        except Exception:
            pass


def _open_count_today() -> int:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()
    return n


def _open_positions() -> int:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchone()[0]
    conn.close()
    return n


def _determine_bias(flow: dict, c15m: list, zone: dict) -> str:
    bias = zone.get('bias') if zone.get('active') else ''
    if bias in ('BULLISH', 'BEARISH'):
        return bias

    ema = flow.get('ema') or {}
    if ema.get('status') == 'BULLISH':
        return 'BULLISH'
    if ema.get('status') == 'BEARISH':
        return 'BEARISH'

    from agents.analysis_agent import get_structure
    struct = get_structure(c15m)
    if struct.get('trend') in ('BULLISH', 'BEARISH'):
        return struct['trend']

    oi = flow.get('oi') or {}
    if oi.get('bias') in ('BULLISH', 'BEARISH'):
        return oi['bias']
    return ''


def _score_opportunity(price: float, bias: str, session: str,
                       c5m: list, flow: dict, zone: dict,
                       vwap: float, rsi: float) -> dict:
    """Relaxed entry score — bot explores when enough clues align."""
    sim_score = 0
    reasons = []

    fs = flow.get('flow_score', 0)
    if fs >= 2:
        sim_score += 1
        reasons.append(f'flow {fs}/6')
    if fs >= 4:
        sim_score += 1

    if session in ('MORNING_TREND', 'AFTERNOON_MOVE'):
        sim_score += 2
        reasons.append(session)
    elif session == 'OPENING_CHAOS':
        now = datetime.now(IST)
        if now.hour == 9 and now.minute >= 35:
            sim_score += 1
            reasons.append('post-open settle')

    if vwap and price:
        aligned = (price > vwap if bias == 'BULLISH' else price < vwap)
        if aligned:
            sim_score += 1
            reasons.append('VWAP aligned')

    if len(c5m) >= 3:
        if bias == 'BULLISH' and c5m[-1]['close'] > c5m[-2]['close']:
            sim_score += 1
            reasons.append('5m momentum ↑')
        elif bias == 'BEARISH' and c5m[-1]['close'] < c5m[-2]['close']:
            sim_score += 1
            reasons.append('5m momentum ↓')

    if zone.get('active'):
        low, high = zone.get('low', 0), zone.get('high', 0)
        if low and high and low * 0.995 <= price <= high * 1.005:
            sim_score += 2
            reasons.append(f'zone {low:,.0f}–{high:,.0f}')

    from agents.analysis_agent import check_choch
    ch = check_choch(c5m, bias)
    if ch.get('confirmed'):
        sim_score += 2
        reasons.append('CHoCH')

    if 35 <= rsi <= 68:
        sim_score += 1

    return {
        'ok': sim_score >= SIM_MIN_SCORE,
        'sim_score': sim_score,
        'reasons': reasons,
        'choch': ch if ch.get('confirmed') else {},
    }


def _build_sim_params(bias: str, price: float) -> dict:
    from src.expiry_picker import next_banknifty_expiry
    from src.premium_feed import virtual_buy_fill, VIRTUAL_REQUIRE_GROWW
    from src.trade_filters import get_dynamic_sl_target

    zone = STATE.get('zone', {}) or {}
    expiry = zone.get('expiry') or next_banknifty_expiry(min_days_ahead=SIM_MIN_DAYS_TO_EXPIRY)
    strike, opt = 0, 'CE' if bias == 'BULLISH' else 'PE'

    if zone.get('active') and zone.get('strike'):
        strike = zone['strike']
        opt = zone.get('opt_type', opt)
        name = zone.get('option_name') or f'BANKNIFTY {strike} {opt}'
    else:
        from src.strike_picker import find_affordable_strike
        picked = find_affordable_strike(price, bias, expiry)
        if picked:
            fill = virtual_buy_fill(picked['strike'], picked['opt_type'], picked['expiry'])
            if fill.get('ok'):
                picked['premium'] = fill['premium']
                picked['prem_source'] = fill['prem_source']
            elif VIRTUAL_REQUIRE_GROWW:
                return {}
            picked.setdefault('prem_source', 'GROWW_LTP')
            return picked
        atm = round(price / 100) * 100
        strike = atm + (100 if bias == 'BULLISH' else -100)
        name = f'BANKNIFTY {strike} {opt}'

    fill = virtual_buy_fill(strike, opt, expiry)
    if not fill.get('ok'):
        if VIRTUAL_REQUIRE_GROWW:
            return {}
        from src.premium_feed import estimate_premium
        prem = estimate_premium(200, price, price, strike, opt)
        prem_source = 'DELTA_MODEL'
    else:
        prem = fill['premium']
        prem_source = fill['prem_source']

    dyn = get_dynamic_sl_target(prem)
    return {
        'name': name,
        'premium': round(prem, 1),
        'prem_source': prem_source,
        'sl_prem': dyn.get('sl_prem', round(prem * 0.7)),
        'tgt_prem': dyn.get('tgt_prem', round(prem * 2)),
        'strike': strike,
        'opt_type': opt,
        'expiry': expiry,
        'range_note': f"Groww LTP virtual buy @ ₹{prem} | BNF {price:,.0f}",
    }


def _attach_snapshot(result: dict) -> dict:
    """Add market context for scan journal / visibility reports."""
    try:
        from src.sim_scan_journal import market_snapshot
        result['snapshot'] = market_snapshot()
    except Exception:
        pass
    return result


def evaluate_explore_setup() -> dict:
    """Score current market for a virtual CE/PE drill."""
    from src.shadow_learning import is_sim_phase

    if not SIM_ENABLED or not is_sim_phase():
        return _attach_snapshot({'ok': False, 'reason': 'sim off or past sim phase'})

    if STATE.get('system.paused'):
        return _attach_snapshot({'ok': False, 'reason': 'paused'})

    price = STATE.get('market.price', 0)
    c5m = STATE.get('market.candles_5m', [])
    c15m = STATE.get('market.candles_15m', [])
    session = STATE.get('market.session', 'CLOSED')
    vwap = STATE.get('market.vwap', 0)
    rsi = STATE.get('market.rsi_5m', 50)
    zone = STATE.get('zone', {}) or {}
    flow = STATE.get('market.flow') or STATE.get('signals.market_flow') or {}

    if price <= 0 or len(c5m) < 5:
        return _attach_snapshot({
            'ok': False,
            'reason': f"warming up ({len(c5m)}/5 candles)",
            'sim_score': 0,
        })

    now = datetime.now(IST)
    end_t = dtime(14, 0) if SIM_ALIGN_EXECUTE else dtime(14, 45)
    if not (dtime(9, 20) <= now.time() <= end_t):
        return _attach_snapshot({'ok': False, 'reason': 'outside sim window'})

    if SIM_SKIP_CHOP_SESSIONS and session in _CHOP_SESSIONS:
        return _attach_snapshot({
            'ok': False,
            'reason': f'chop session {session}',
            'sim_score': 0,
            'session': session,
        })

    bias = _determine_bias(flow, c15m, zone)
    if bias not in ('BULLISH', 'BEARISH'):
        return _attach_snapshot({
            'ok': False,
            'reason': 'no clear bias',
            'bias': bias or 'NEUTRAL',
            'sim_score': 0,
        })

    scored = _score_opportunity(price, bias, session, c5m, flow, zone, vwap, rsi)
    if not scored['ok']:
        return _attach_snapshot({
            'ok': False,
            'reason': f"score {scored['sim_score']}<{SIM_MIN_SCORE}",
            'bias': bias,
            'sim_score': scored['sim_score'],
            'reasons': scored['reasons'],
            'session': session,
            'price': price,
            'flow_score': flow.get('flow_score', 0),
        })

    params = _build_sim_params(bias, price)
    if not params.get('premium'):
        return _attach_snapshot({
            'ok': False,
            'reason': 'no premium (Groww LTP required)',
            'bias': bias,
            'sim_score': scored['sim_score'],
            'reasons': scored['reasons'],
            'session': session,
            'price': price,
            'flow_score': flow.get('flow_score', 0),
        })

    realism = check_sim_entry_gates(params.get('premium', 0), scored['sim_score'])
    if not realism.get('ok'):
        return _attach_snapshot({
            'ok': False,
            'reason': realism.get('reason', 'realism gate'),
            'bias': bias,
            'sim_score': scored['sim_score'],
            'reasons': scored['reasons'],
            'session': session,
            'price': price,
            'flow_score': flow.get('flow_score', 0),
        })

    return _attach_snapshot({
        'ok': True,
        'bias': bias,
        'price': price,
        'session': session,
        'regime': STATE.get('market.regime', 'TRENDING'),
        'sim_score': scored['sim_score'],
        'reasons': scored['reasons'],
        'params': params,
        'flow_score': flow.get('flow_score', 0),
    })


def open_explore_sim(setup: dict) -> dict:
    """Insert virtual trade into shadow_trades — memory only."""
    global _last_open_at

    if _open_count_today() >= SIM_MAX_PER_DAY:
        return {'opened': False, 'reason': f'max {SIM_MAX_PER_DAY}/day'}
    if _open_positions() >= SIM_MAX_OPEN:
        return {'opened': False, 'reason': f'max {SIM_MAX_OPEN} open'}

    if _last_open_at:
        gap = (datetime.now(IST) - _last_open_at).total_seconds() / 60
        if gap < SIM_MIN_GAP_MIN:
            return {'opened': False, 'reason': f'wait {SIM_MIN_GAP_MIN}m'}

    from src.shadow_learning import init_shadow_tables
    init_shadow_tables()

    params = setup['params']
    today = datetime.now(IST).strftime('%Y-%m-%d')
    now = datetime.now(IST)
    reasons_txt = '; '.join(setup.get('reasons', [])[:4])
    prediction = (
        f"{setup['bias']} sim — BNF {setup['price']:,.0f} | "
        f"{params['name']} @ ₹{params['premium']} → tgt ₹{params['tgt_prem']}"
    )

    conn = _conn()
    conn.execute("""
        INSERT INTO shadow_trades (
            date, entry_time, option_name, bias, session, score, regime,
            bnf_entry, strike, opt_type, expiry, entry_prem, sl_prem, tgt_prem,
            prediction, rag_notes, status, sim_source, sim_score, range_note,
            entry_reasons, mae_prem, mfe_prem, peak_pnl_rs, prem_source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        today, now.strftime('%H:%M'),
        params['name'], setup['bias'], setup.get('session', ''),
        setup.get('sim_score', 0), setup.get('regime', ''),
        setup['price'], params.get('strike', 0),
        params.get('opt_type', 'CE'), params.get('expiry', ''),
        params['premium'], params['sl_prem'], params['tgt_prem'],
        prediction, 'market_sim', 'OPEN', 'EXPLORE',
        setup.get('sim_score', 0), params.get('range_note', ''),
        reasons_txt, params['premium'], params['premium'], 0.0,
        params.get('prem_source', 'DELTA_MODEL'),
    ))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    try:
        from src.virtual_broker import record_sim_entry
        record_sim_entry(sid, setup['bias'], params['premium'], params)
    except Exception:
        pass

    _last_open_at = now
    prem_tag = '📡 live Groww LTP' if params.get('prem_source') == 'GROWW_LTP' else '📐 delta model'
    _notify(
        f"🎮 *Virtual order #{sid}* — Groww LTP, no real buy\n"
        f"*{params['name']}* @ ₹{params['premium']} ({prem_tag})\n"
        f"Score {setup.get('sim_score')} | {setup['bias']} | {setup.get('session')}\n"
        f"📋 {params.get('range_note', '')}\n"
        f"🎯 Target ₹{params['tgt_prem']} | SL ₹{params['sl_prem']}\n"
        f"_Watching Groww LTP live (~10s) — profit/loss from real premium_",
        kind='open',
    )
    return {'opened': True, 'id': sid, 'name': params['name']}


def scan_and_maybe_open() -> dict:
    """Periodic scan — bot picks a virtual trade when it feels right."""
    global _last_scan_at

    now = datetime.now(IST)
    if _last_scan_at:
        mins = (now - _last_scan_at).total_seconds() / 60
        if mins < SIM_SCAN_MINUTES:
            return {'scanned': False, 'reason': 'cooldown'}

    _last_scan_at = now
    setup = evaluate_explore_setup()
    if not setup.get('ok'):
        result = {
            'scanned': True,
            'opened': False,
            'reason': setup.get('reason'),
            'sim_score': setup.get('sim_score', 0),
            'bias': setup.get('bias', ''),
            'reasons': setup.get('reasons', []),
            'snapshot': setup.get('snapshot'),
        }
        _log_scan(result)
        try:
            from src.sim_market_learn import log_sim_learning
            log_sim_learning(result)
        except Exception:
            pass
        return result

    result = open_explore_sim(setup)
    result['scanned'] = True
    result['sim_score'] = setup.get('sim_score', 0)
    result['bias'] = setup.get('bias', '')
    result['reasons'] = setup.get('reasons', [])
    result['snapshot'] = setup.get('snapshot')
    _log_scan(result)
    try:
        from src.sim_market_learn import log_sim_learning
        log_sim_learning(result)
    except Exception:
        pass
    return result


def _log_scan(result: dict):
    try:
        from src.sim_scan_journal import log_sim_scan
        log_sim_scan(result)
    except Exception:
        pass


def format_sim_status() -> str:
    from src.shadow_learning import learning_phase_info, get_today_shadow_trades

    info = learning_phase_info()
    trades = get_today_shadow_trades()
    explore = [t for t in trades if t.get('sim_source') == 'EXPLORE']
    open_e = [t for t in explore if t.get('status') == 'OPEN']
    closed = [t for t in explore if t.get('status') == 'CLOSED']
    wins = sum(1 for t in closed if t.get('outcome') == 'WIN')

    if info['phase'] != 'SIM':
        if info['phase'] == 'PAPER':
            return "🎮 Market sim: *paused* — week 3–4 paper training (`/execute`)"
        return "🎮 Market sim: *paused* — month complete; check `/readiness`"

    return (
        f"🎮 *Market sim* (week 1–2, {info['days_left']}d left)\n"
        f"Today: {len(explore)} drills ({len(open_e)} open) | "
        f"{wins}W / {len(closed)-wins}L closed\n"
        f"Cap: {SIM_MAX_OPEN} open, {SIM_MAX_PER_DAY}/day | "
        f"min score {SIM_MIN_SCORE}\n"
        f"_Virtual CE/PE on live flow — no money_"
    )
