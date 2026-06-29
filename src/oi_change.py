"""
OI change % — fresh writing vs unwinding at key strikes.
Compares current NSE chain to last snapshot (same session).
"""

import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')


def _conn():
    return sqlite3.connect(DB_FILE)


def init_oi_snapshots():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            strike INTEGER,
            ce_oi INTEGER DEFAULT 0,
            pe_oi INTEGER DEFAULT 0,
            total_ce_oi INTEGER DEFAULT 0,
            total_pe_oi INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _parse_chain(raw: dict) -> dict:
    records = (raw or {}).get('records', {})
    data = records.get('data', [])
    spot = records.get('underlyingValue', 0)
    strikes = {}
    total_ce, total_pe = 0, 0
    for row in data:
        sp = row.get('strikePrice', 0)
        ce = row.get('CE', {}) or {}
        pe = row.get('PE', {}) or {}
        ce_oi = int(ce.get('openInterest', 0) or 0)
        pe_oi = int(pe.get('openInterest', 0) or 0)
        if sp:
            strikes[sp] = {
                'ce_oi': ce_oi, 'pe_oi': pe_oi,
                'ce_chg': int(ce.get('changeinOpenInterest', 0) or 0),
                'pe_chg': int(pe.get('changeinOpenInterest', 0) or 0),
            }
            total_ce += ce_oi
            total_pe += pe_oi
    return {'strikes': strikes, 'total_ce': total_ce, 'total_pe': total_pe, 'spot': spot}


def save_oi_snapshot(raw: dict) -> bool:
    init_oi_snapshots()
    parsed = _parse_chain(raw)
    if not parsed['strikes']:
        return False
    now = datetime.now(IST)
    conn = _conn()
    conn.execute(
        "DELETE FROM oi_snapshots WHERE date=?",
        (now.strftime('%Y-%m-%d'),),
    )
    for strike, oi in parsed['strikes'].items():
        conn.execute("""
            INSERT INTO oi_snapshots (date, time, strike, ce_oi, pe_oi, total_ce_oi, total_pe_oi)
            VALUES (?,?,?,?,?,?,?)
        """, (
            now.strftime('%Y-%m-%d'), now.strftime('%H:%M'),
            strike, oi['ce_oi'], oi['pe_oi'],
            parsed['total_ce'], parsed['total_pe'],
        ))
    conn.commit()
    conn.close()
    return True


def analyse_oi_change(bias: str = 'BULLISH', price: float = 0) -> dict:
    """OI change at walls + session build from NSE changeinOpenInterest."""
    from src.oi_analysis import get_oi_data, calculate_max_pain

    raw = get_oi_data()
    if not raw:
        return {'available': False, 'reason': 'OI data unavailable'}

    parsed = _parse_chain(raw)
    mp = calculate_max_pain(raw)
    if not mp.get('available'):
        return {'available': False}

    strikes = parsed['strikes']
    res = mp.get('resistance', 0)
    sup = mp.get('support', 0)

    ce_chg_at_res = strikes.get(res, {}).get('ce_chg', 0)
    pe_chg_at_sup = strikes.get(sup, {}).get('pe_chg', 0)
    total_ce_chg = sum(v.get('ce_chg', 0) for v in strikes.values())
    total_pe_chg = sum(v.get('pe_chg', 0) for v in strikes.values())

    warnings, supports, score = [], [], 0
    block = False

    if bias == 'BULLISH' and ce_chg_at_res > 50000:
        warnings.append(
            f"📈 Fresh CE writing at resistance {res:,} (+{ce_chg_at_res:,} OI) — wall building"
        )
        if ce_chg_at_res > 150000:
            block = True
    elif bias == 'BULLISH' and ce_chg_at_res < -30000:
        supports.append(f"✅ CE unwinding at {res:,} — resistance weakening")

    if bias == 'BEARISH' and pe_chg_at_sup > 50000:
        warnings.append(
            f"📉 Fresh PE writing at support {sup:,} (+{pe_chg_at_sup:,} OI) — floor building"
        )
        if pe_chg_at_sup > 150000:
            block = True
    elif bias == 'BEARISH' and pe_chg_at_sup < -30000:
        supports.append(f"✅ PE unwinding at {sup:,} — support weakening")

    if total_pe_chg > total_ce_chg * 1.3 and bias == 'BULLISH':
        supports.append(f"✅ Put OI building faster — bullish hedge (PCR support)")
        score += 1
    if total_ce_chg > total_pe_chg * 1.3 and bias == 'BEARISH':
        supports.append(f"✅ Call OI building faster — bearish hedge")
        score += 1

    pcr_chg_note = f"CE Δ{total_ce_chg:+,} | PE Δ{total_pe_chg:+,}"

    result = {
        'available': True,
        'ce_chg_at_resistance': ce_chg_at_res,
        'pe_chg_at_support': pe_chg_at_sup,
        'total_ce_chg': total_ce_chg,
        'total_pe_chg': total_pe_chg,
        'resistance': res,
        'support': sup,
        'block': block,
        'score': score,
        'warnings': warnings,
        'supports': supports,
        'summary': pcr_chg_note,
    }

    init_oi_snapshots()
    save_oi_snapshot(raw)
    return result


def format_oi_change_block(oi_chg: dict) -> str:
    if not oi_chg.get('available'):
        return ''
    lines = ["\n*OI change (session):*", f"  {oi_chg.get('summary', '')}"]
    for s in oi_chg.get('supports', [])[:2]:
        lines.append(f"  {s}")
    for w in oi_chg.get('warnings', [])[:2]:
        lines.append(f"  {w}")
    return '\n'.join(lines)
