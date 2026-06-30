"""
Option Greeks — NSE chain IV + Black-Scholes (delta, gamma, theta, vega).

Uses NSE option chain (throttled ~15 min) for ATM implied vol.
Computes greeks for virtual/paper trades; feeds ML + dashboard.
"""

import json
import math
import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')
GREEKS_ENABLED = os.getenv('GREEKS_ENABLED', 'true').lower() == 'true'
CHAIN_REFRESH_SEC = int(os.getenv('CHAIN_REFRESH_SEC', '900'))
RISK_FREE_RATE = float(os.getenv('RISK_FREE_RATE', '0.065'))
LOT_SIZE = 15


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def black_scholes_greeks(
    spot: float, strike: float, dte_days: float, iv: float,
    opt_type: str = 'CE', rate: float = None,
) -> dict:
    """
    European BS greeks (approximation for Indian index options).
    iv: decimal (0.18 = 18%). Returns per-share greeks + lot-scaled theta/vega.
    """
    rate = rate if rate is not None else RISK_FREE_RATE
    T = max(dte_days, 0.01) / 365.0
    sigma = max(iv, 0.05)
    K = strike
    S = spot

    d1, d2 = _d1_d2(S, K, T, rate, sigma)
    if d1 == 0 and d2 == 0:
        return _empty_greeks('invalid inputs')

    pdf1 = _norm_pdf(d1)
    is_call = opt_type.upper() in ('CE', 'CALL', 'C')

    if is_call:
        delta = _norm_cdf(d1)
        theta = (
            -(S * pdf1 * sigma) / (2 * math.sqrt(T))
            - rate * K * math.exp(-rate * T) * _norm_cdf(d2)
        ) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -(S * pdf1 * sigma) / (2 * math.sqrt(T))
            + rate * K * math.exp(-rate * T) * _norm_cdf(-d2)
        ) / 365.0

    gamma = pdf1 / (S * sigma * math.sqrt(T))
    vega = S * pdf1 * math.sqrt(T) / 100.0  # per 1% IV move

    return {
        'delta': round(delta, 4),
        'gamma': round(gamma, 6),
        'theta_per_day': round(theta, 2),
        'theta_per_lot_day': round(theta * LOT_SIZE, 0),
        'vega_per_1pct': round(vega, 2),
        'vega_per_lot_1pct': round(vega * LOT_SIZE, 0),
        'iv_pct': round(sigma * 100, 2),
        'dte_days': round(dte_days, 2),
        'model': 'black_scholes',
    }


def _empty_greeks(reason: str = '') -> dict:
    return {
        'delta': 0, 'gamma': 0, 'theta_per_day': 0, 'theta_per_lot_day': 0,
        'vega_per_1pct': 0, 'vega_per_lot_1pct': 0, 'iv_pct': 0,
        'dte_days': 0, 'model': reason or 'unavailable',
    }


def implied_vol_newton(
    spot: float, strike: float, dte_days: float, market_price: float,
    opt_type: str = 'CE', rate: float = None, max_iter: int = 40,
) -> float:
    """Solve IV from market premium (decimal IV)."""
    if market_price <= 0 or spot <= 0 or strike <= 0:
        return 0.18
    rate = rate if rate is not None else RISK_FREE_RATE
    T = max(dte_days, 0.01) / 365.0
    sigma = 0.25
    for _ in range(max_iter):
        g = black_scholes_greeks(spot, strike, dte_days, sigma, opt_type, rate)
        d1, d2 = _d1_d2(spot, strike, T, rate, sigma)
        if d1 == 0:
            break
        is_call = opt_type.upper() in ('CE', 'CALL', 'C')
        if is_call:
            price = spot * _norm_cdf(d1) - strike * math.exp(-rate * T) * _norm_cdf(d2)
        else:
            price = strike * math.exp(-rate * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        vega = spot * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-8:
            break
        diff = market_price - price
        if abs(diff) < 0.05:
            return max(sigma, 0.05)
        sigma = max(0.05, min(3.0, sigma + diff / vega))
    return sigma


def _conn():
    from src.db_persistence import connect
    return connect()


def init_greeks_tables():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS option_iv_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT,
            time      TEXT,
            spot      REAL,
            atm_iv_ce REAL,
            atm_iv_pe REAL,
            atm_strike REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT,
            time      TEXT,
            spot      REAL,
            snapshot_json TEXT
        )
    """)
    conn.commit()
    conn.close()


def _find_chain_row(oi_data: dict, strike: float, spot: float) -> dict:
    if not oi_data:
        return {}
    data = oi_data.get('records', {}).get('data', [])
    if not data:
        return {}
    if strike:
        for row in data:
            if row.get('strikePrice') == strike:
                return row
    atm = round(spot / 100) * 100
    best, best_dist = None, 99999
    for row in data:
        sp = row.get('strikePrice', 0)
        dist = abs(sp - atm)
        if dist < best_dist:
            best_dist, best = dist, row
    return best or {}


def _greeks_from_nse_leg(leg: dict) -> dict:
    """Prefer NSE-published greeks when present."""
    if not leg:
        return {}
    d = float(leg.get('delta') or 0)
    if abs(d) < 0.001:
        return {}
    return {
        'delta': round(d, 4),
        'gamma': round(float(leg.get('gamma') or 0), 6),
        'theta_per_day': round(float(leg.get('theta') or 0), 2),
        'theta_per_lot_day': round(float(leg.get('theta') or 0) * LOT_SIZE, 0),
        'vega_per_1pct': round(float(leg.get('vega') or 0), 2),
        'vega_per_lot_1pct': round(float(leg.get('vega') or 0) * LOT_SIZE, 0),
        'iv_pct': round(float(leg.get('impliedVolatility') or 0), 2),
        'model': 'nse_chain',
    }


def _parse_atm_iv(oi_data: dict, spot: float) -> dict:
    """ATM IV from NSE chain CE/PE impliedVolatility fields."""
    if not oi_data:
        return {}
    records = oi_data.get('records', {})
    data = records.get('data', [])
    if not data or not spot:
        return {}

    atm = round(spot / 100) * 100
    best = None
    best_dist = 99999
    for row in data:
        sp = row.get('strikePrice', 0)
        if not sp:
            continue
        dist = abs(sp - atm)
        if dist < best_dist:
            best_dist = dist
            best = row

    if not best:
        return {}

    ce = best.get('CE', {}) or {}
    pe = best.get('PE', {}) or {}
    iv_ce = float(ce.get('impliedVolatility') or 0)
    iv_pe = float(pe.get('impliedVolatility') or 0)
    if iv_ce > 3:
        iv_ce /= 100.0
    if iv_pe > 3:
        iv_pe /= 100.0

    return {
        'atm_strike': best.get('strikePrice'),
        'iv_ce': iv_ce,
        'iv_pe': iv_pe,
        'iv_avg': round((iv_ce + iv_pe) / 2, 4) if iv_ce and iv_pe else iv_ce or iv_pe,
        'ce_ltp': float(ce.get('lastPrice') or 0),
        'pe_ltp': float(pe.get('lastPrice') or 0),
        'ce_greeks': _greeks_from_nse_leg(ce),
        'pe_greeks': _greeks_from_nse_leg(pe),
    }


def _iv_rank(atm_iv: float) -> float:
    """IV rank vs last 30 stored ATM IV readings (0–100)."""
    if atm_iv <= 0:
        return 50.0
    try:
        conn = _conn()
        rows = conn.execute("""
            SELECT atm_iv_ce FROM option_iv_history
            WHERE atm_iv_ce > 0 ORDER BY id DESC LIMIT 120
        """).fetchall()
        conn.close()
        vals = [r[0] for r in rows if r[0]]
        if len(vals) < 5:
            return 50.0
        lo, hi = min(vals), max(vals)
        if hi <= lo:
            return 50.0
        return round((atm_iv - lo) / (hi - lo) * 100, 1)
    except Exception:
        return 50.0


def refresh_chain_snapshot(force: bool = False) -> dict:
    """
    Fetch NSE chain (throttled), cache in STATE, log ATM IV.
  No Groww API calls.
    """
    from core.shared_state import STATE

    if not GREEKS_ENABLED:
        return {'ok': False, 'reason': 'greeks disabled'}

    try:
        from src.api_scheduler import should_fetch, mark_fetched
        if not force and not should_fetch('nse_chain_greeks', CHAIN_REFRESH_SEC):
            cached = STATE.get('market.option_chain') or {}
            if cached:
                return {'ok': True, 'cached': True, **cached}
    except Exception:
        pass

    spot = float(STATE.get('market.price', 0) or 0)
    try:
        from src.oi_analysis import get_oi_data, calculate_max_pain
        raw = get_oi_data()
        if not raw:
            return {'ok': False, 'reason': 'NSE chain unavailable'}

        oi = calculate_max_pain(raw)
        atm = _parse_atm_iv(raw, spot)
        mark_fetched('nse_chain_greeks')

        snap = {
            'ok': True,
            'spot': spot,
            'timestamp': datetime.now(IST).strftime('%H:%M:%S'),
            'max_pain': oi.get('max_pain'),
            'pcr': oi.get('pcr'),
            'resistance': oi.get('resistance'),
            'support': oi.get('support'),
            'atm_strike': atm.get('atm_strike'),
            'atm_iv_ce': atm.get('iv_ce'),
            'atm_iv_pe': atm.get('iv_pe'),
            'atm_iv_avg': atm.get('iv_avg'),
            'iv_rank': _iv_rank(atm.get('iv_avg') or 0),
            'atm_ce_greeks': atm.get('ce_greeks') or {},
            'atm_pe_greeks': atm.get('pe_greeks') or {},
        }
        STATE.set('market.option_chain', snap)
        STATE.set('market.oi_chain_raw', raw)

        init_greeks_tables()
        now = datetime.now(IST)
        conn = _conn()
        conn.execute("""
            INSERT INTO option_iv_history (date, time, spot, atm_iv_ce, atm_iv_pe, atm_strike)
            VALUES (?,?,?,?,?,?)
        """, (
            now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
            spot, atm.get('iv_ce') or 0, atm.get('iv_pe') or 0, atm.get('atm_strike') or 0,
        ))
        conn.execute("""
            INSERT INTO option_chain_snapshots (date, time, spot, snapshot_json)
            VALUES (?,?,?,?)
        """, (
            now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
            spot, json.dumps(snap, default=str),
        ))
        conn.commit()
        conn.close()
        return snap
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:80]}


def greeks_for_contract(
    strike: float, opt_type: str, expiry: str, premium: float = 0,
) -> dict:
    """Greeks for one contract — NSE chain first, Black-Scholes fallback."""
    from core.shared_state import STATE
    from src.expiry_picker import days_to_expiry

    spot = float(STATE.get('market.price', 0) or 0)
    if not spot or not strike:
        return _empty_greeks('no spot/strike')

    dte = float(days_to_expiry(expiry))
    chain = STATE.get('market.option_chain') or {}
    raw = STATE.get('market.oi_chain_raw')

    if raw:
        row = _find_chain_row(raw, strike, spot)
        leg = (row.get('CE') if opt_type.upper() in ('CE', 'CALL') else row.get('PE')) or {}
        nse_g = _greeks_from_nse_leg(leg)
        if nse_g:
            nse_g['dte_days'] = round(dte, 2)
            nse_g['iv_rank'] = chain.get('iv_rank', 50)
            nse_g['spot'] = spot
            nse_g['strike'] = strike
            nse_g['opt_type'] = opt_type
            nse_g['expiry'] = expiry
            nse_g['iv_source'] = 'nse_chain'
            return nse_g

    iv = float(chain.get('atm_iv_avg') or 0)
    if opt_type.upper() in ('CE', 'CALL'):
        iv = float(chain.get('atm_iv_ce') or iv)
    else:
        iv = float(chain.get('atm_iv_pe') or iv)
    if iv <= 0:
        iv = 0.18

    g = black_scholes_greeks(spot, strike, dte, iv, opt_type)
    g['iv_rank'] = chain.get('iv_rank', 50)
    g['spot'] = spot
    g['strike'] = strike
    g['opt_type'] = opt_type
    g['expiry'] = expiry
    g['iv_source'] = 'black_scholes_approx'
    return g


def get_greeks_dashboard() -> dict:
    """Payload for dashboard Greeks card."""
    from core.shared_state import STATE
    chain = STATE.get('market.option_chain') or {}
    if not chain.get('ok'):
        refresh_chain_snapshot(force=False)
        chain = STATE.get('market.option_chain') or {}

    spot = float(STATE.get('market.price', 0) or 0)
    zone = STATE.get('zone', {}) or {}
    strike = zone.get('strike') or (round(spot / 100) * 100 if spot else 0)
    opt = zone.get('opt_type', 'CE')
    expiry = zone.get('expiry', '')
    prem = float(zone.get('premium', 0) or 0)

    contract = {}
    if strike and expiry:
        contract = greeks_for_contract(strike, opt, expiry, prem)
    elif chain.get('atm_ce_greeks'):
        contract = chain.get('atm_ce_greeks') or {}
        contract = dict(contract)
        contract['opt_type'] = 'CE'
        contract['strike'] = chain.get('atm_strike')

    return {
        'chain': chain,
        'contract': contract,
        'math_note': (
            'Delta=₹ move per ₹1 BNF · Theta=decay/day/lot · '
            'Vega=₹ per 1% IV · IV from NSE chain (15m cache)'
        ),
    }


def format_greeks_lesson(g: dict) -> str:
    if not g or not g.get('delta'):
        return ''
    return (
        f"δ{g.get('delta', 0):.2f} θ₹{g.get('theta_per_lot_day', 0):.0f}/d "
        f"ν₹{g.get('vega_per_lot_1pct', 0):.0f}/1%IV "
        f"IV{g.get('iv_pct', 0):.0f}% rank{g.get('iv_rank', 50):.0f}"
    )
