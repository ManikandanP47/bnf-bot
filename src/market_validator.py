"""
Market Validator — 3 Extra Confirmation Layers
Runs BEFORE any trade is approved.

Layer 1: India VIX check (yfinance ^INDIAVIX)
         VIX > 20 = high fear = skip trade
         VIX < 15 = calm = green light

Layer 2: NSE Options OI check (free NSE API)
         Are big players buying CE or PE?
         OI must agree with bot's bias

Layer 3: EMA trend confirmation (yfinance daily data)
         Price above EMA20 + EMA50 = strong bullish
         Price below both = strong bearish
         Against trend = skip

These 3 checks add up to +6 score points if all pass.
If any hard-fails, trade is blocked entirely.
"""

import warnings
warnings.filterwarnings('ignore')

import requests
import pytz
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    LIBS_OK = True
except ImportError:
    LIBS_OK = False

IST = pytz.timezone('Asia/Kolkata')


# ─────────────────────────────────────────────────────────────────
# LAYER 1: INDIA VIX CHECK
# ─────────────────────────────────────────────────────────────────

def check_vix() -> dict:
    """
    India VIX from yfinance.
    VIX = fear index. High VIX = options expensive + unpredictable.

    Rules:
      VIX < 13  = Very calm  → +2 score, strong green
      VIX 13-17 = Normal     → +1 score, proceed
      VIX 17-20 = Caution    →  0 score, warning
      VIX > 20  = High fear  → BLOCK trade
      VIX > 25  = Extreme    → BLOCK trade (hard stop)
    """
    if not LIBS_OK:
        return {'status': 'UNKNOWN', 'score': 0,
                'reason': '⚠️ VIX check unavailable'}
    try:
        vix_data = yf.Ticker('^INDIAVIX').history(period='3d', interval='1d')
        if vix_data is None or len(vix_data) == 0:
            return {'status': 'UNKNOWN', 'score': 0,
                    'reason': '⚠️ VIX data unavailable — proceeding'}

        vix = round(float(vix_data['Close'].iloc[-1]), 2)

        if vix > 25:
            return {
                'status': 'BLOCK',
                'vix':    vix,
                'score':  0,
                'reason': f'🚫 VIX {vix} — extreme fear, skip trade'
            }
        elif vix > 20:
            return {
                'status': 'BLOCK',
                'vix':    vix,
                'score':  0,
                'reason': f'🚫 VIX {vix} — high fear, options too expensive'
            }
        elif vix > 17:
            return {
                'status': 'CAUTION',
                'vix':    vix,
                'score':  0,
                'reason': f'⚠️ VIX {vix} — elevated, trade carefully'
            }
        elif vix > 13:
            return {
                'status': 'OK',
                'vix':    vix,
                'score':  1,
                'reason': f'✅ VIX {vix} — normal range'
            }
        else:
            return {
                'status': 'GREAT',
                'vix':    vix,
                'score':  2,
                'reason': f'✅ VIX {vix} — very calm, ideal conditions'
            }

    except Exception as e:
        return {
            'status': 'UNKNOWN',
            'score':  0,
            'reason': f'⚠️ VIX fetch failed — proceeding ({str(e)[:30]})'
        }


# ─────────────────────────────────────────────────────────────────
# LAYER 2: NSE OPTIONS OI CHECK
# ─────────────────────────────────────────────────────────────────

def check_oi(trend: str, current_price: float) -> dict:
    """
    NSE free API — checks Put/Call OI ratio near ATM strikes.
    Big players write options where they expect price NOT to go.
    High PE OI at a level = support (bullish signal)
    High CE OI at a level = resistance (bearish signal)

    PCR (Put/Call Ratio):
      PCR > 1.2 = More puts = market expects support = BULLISH ✅
      PCR 0.8-1.2 = Neutral
      PCR < 0.8  = More calls = market expects resistance = BEARISH
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.nseindia.com',
        }

        session = requests.Session()
        # First hit NSE homepage to get cookies
        session.get('https://www.nseindia.com', headers=headers, timeout=10)

        # Then fetch options chain
        resp = session.get(
            'https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY',
            headers=headers,
            timeout=15
        )

        if resp.status_code != 200:
            return {
                'status': 'UNKNOWN',
                'score':  0,
                'reason': '⚠️ NSE OI unavailable — proceeding'
            }

        data   = resp.json()
        records = data.get('records', {}).get('data', [])

        if not records:
            return {
                'status': 'UNKNOWN',
                'score':  0,
                'reason': '⚠️ OI data empty — proceeding'
            }

        # Focus on ATM ± 500 strikes
        atm    = round(current_price / 100) * 100
        nearby = [
            r for r in records
            if r.get('strikePrice') and
            abs(r['strikePrice'] - atm) <= 500
        ]

        total_ce_oi = sum(
            r.get('CE', {}).get('openInterest', 0)
            for r in nearby if r.get('CE')
        )
        total_pe_oi = sum(
            r.get('PE', {}).get('openInterest', 0)
            for r in nearby if r.get('PE')
        )

        if total_ce_oi == 0:
            return {
                'status': 'UNKNOWN',
                'score':  0,
                'reason': '⚠️ OI calculation failed — proceeding'
            }

        pcr = round(total_pe_oi / total_ce_oi, 2)

        # Find max pain (strike with highest total OI = where price tends to go)
        strike_pain = {}
        for r in nearby:
            strike = r.get('strikePrice', 0)
            ce_oi  = r.get('CE', {}).get('openInterest', 0)
            pe_oi  = r.get('PE', {}).get('openInterest', 0)
            strike_pain[strike] = ce_oi + pe_oi

        max_pain_strike = max(strike_pain, key=strike_pain.get) if strike_pain else atm

        # Score based on trend alignment
        if trend == 'BULLISH':
            if pcr >= 1.2:
                score  = 2
                status = 'ALIGNED'
                reason = f'✅ PCR {pcr} — put writers supporting market (bullish OI)'
            elif pcr >= 0.8:
                score  = 1
                status = 'NEUTRAL'
                reason = f'⚠️ PCR {pcr} — neutral OI, proceed with caution'
            else:
                score  = 0
                status = 'AGAINST'
                reason = f'⚠️ PCR {pcr} — call writers dominant, resistance ahead'
        else:  # BEARISH
            if pcr <= 0.8:
                score  = 2
                status = 'ALIGNED'
                reason = f'✅ PCR {pcr} — call writers dominant (bearish OI)'
            elif pcr <= 1.2:
                score  = 1
                status = 'NEUTRAL'
                reason = f'⚠️ PCR {pcr} — neutral OI, proceed with caution'
            else:
                score  = 0
                status = 'AGAINST'
                reason = f'⚠️ PCR {pcr} — put writers supporting, bounce likely'

        return {
            'status':          status,
            'pcr':             pcr,
            'ce_oi':           total_ce_oi,
            'pe_oi':           total_pe_oi,
            'max_pain_strike': max_pain_strike,
            'score':           score,
            'reason':          reason
        }

    except Exception as e:
        return {
            'status': 'UNKNOWN',
            'score':  0,
            'reason': f'⚠️ OI fetch failed — proceeding ({str(e)[:30]})'
        }


# ─────────────────────────────────────────────────────────────────
# LAYER 3: EMA TREND CONFIRMATION
# ─────────────────────────────────────────────────────────────────

def check_ema(trend: str) -> dict:
    """
    Daily EMA20 and EMA50 from yfinance.
    Price must be on the RIGHT side of both EMAs.

    Bullish trade rules:
      Price > EMA20 > EMA50 = strong uptrend    → +2 score
      Price > EMA20 only    = weak uptrend      → +1 score
      Price < EMA20         = against trend     → 0 score (warning)
      Price < EMA50         = strongly against  → BLOCK

    Bearish trade rules: opposite
    """
    if not LIBS_OK:
        return {'status': 'UNKNOWN', 'score': 0,
                'reason': '⚠️ EMA check unavailable'}
    try:
        df = yf.Ticker('^NSEBANK').history(period='3mo', interval='1d')
        if df is None or len(df) < 50:
            return {
                'status': 'UNKNOWN',
                'score':  0,
                'reason': '⚠️ EMA data insufficient — proceeding'
            }

        closes       = df['Close']
        current      = float(closes.iloc[-1])
        ema20        = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50        = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
        ema20_prev   = float(closes.ewm(span=20, adjust=False).mean().iloc[-2])
        ema50_prev   = float(closes.ewm(span=50, adjust=False).mean().iloc[-2])

        # EMA slope (rising or falling)
        ema20_rising = ema20 > ema20_prev
        ema50_rising = ema50 > ema50_prev

        if trend == 'BULLISH':
            if current > ema20 and ema20 > ema50 and ema20_rising:
                return {
                    'status': 'STRONG',
                    'score':  2,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'✅ Price {current:,.0f} > EMA20 {ema20:,.0f} > EMA50 {ema50:,.0f} — strong uptrend'
                }
            elif current > ema20:
                return {
                    'status': 'OK',
                    'score':  1,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'✅ Price above EMA20 {ema20:,.0f} — mild uptrend'
                }
            elif current < ema50:
                return {
                    'status': 'BLOCK',
                    'score':  0,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'🚫 Price {current:,.0f} below EMA50 {ema50:,.0f} — avoid CE'
                }
            else:
                return {
                    'status': 'WEAK',
                    'score':  0,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'⚠️ Price between EMA20/50 — weak structure for CE'
                }

        else:  # BEARISH
            if current < ema20 and ema20 < ema50 and not ema20_rising:
                return {
                    'status': 'STRONG',
                    'score':  2,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'✅ Price {current:,.0f} < EMA20 {ema20:,.0f} < EMA50 {ema50:,.0f} — strong downtrend'
                }
            elif current < ema20:
                return {
                    'status': 'OK',
                    'score':  1,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'✅ Price below EMA20 {ema20:,.0f} — mild downtrend'
                }
            elif current > ema50:
                return {
                    'status': 'BLOCK',
                    'score':  0,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'🚫 Price {current:,.0f} above EMA50 {ema50:,.0f} — avoid PE'
                }
            else:
                return {
                    'status': 'WEAK',
                    'score':  0,
                    'ema20':  round(ema20, 0),
                    'ema50':  round(ema50, 0),
                    'reason': f'⚠️ Price between EMA20/50 — weak structure for PE'
                }

    except Exception as e:
        return {
            'status': 'UNKNOWN',
            'score':  0,
            'reason': f'⚠️ EMA check failed — proceeding ({str(e)[:30]})'
        }


# ─────────────────────────────────────────────────────────────────
# MASTER VALIDATOR — runs all 3 layers
# ─────────────────────────────────────────────────────────────────

def validate_trade(trend: str, current_price: float) -> dict:
    """
    Run all 3 validation layers.
    Returns: approved/blocked + score boost + reasons

    Called by Risk Agent before every trade.
    Max additional score = +6 points
    Hard block if VIX > 20 or EMA strongly against trend
    """
    vix_check = check_vix()
    oi_check  = check_oi(trend, current_price)
    ema_check = check_ema(trend)

    total_score = vix_check['score'] + oi_check['score'] + ema_check['score']

    # Hard blocks
    blocked = (
        vix_check.get('status') == 'BLOCK' or
        ema_check.get('status') == 'BLOCK'
    )

    block_reason = ''
    if vix_check.get('status') == 'BLOCK':
        block_reason = vix_check['reason']
    elif ema_check.get('status') == 'BLOCK':
        block_reason = ema_check['reason']

    reasons = [
        vix_check['reason'],
        oi_check['reason'],
        ema_check['reason'],
    ]

    # Build summary for Telegram
    pcr       = oi_check.get('pcr', 'N/A')
    vix_val   = vix_check.get('vix', 'N/A')
    ema20     = ema_check.get('ema20', 'N/A')
    ema50     = ema_check.get('ema50', 'N/A')
    max_pain  = oi_check.get('max_pain_strike', 'N/A')

    summary = (
        f"📊 *Market Validation*\n"
        f"  VIX: {vix_val} | PCR: {pcr}\n"
        f"  EMA20: {ema20:,} | EMA50: {ema50:,}\n"
        f"  Max Pain: {max_pain:,}\n"
        f"  Validator Score: +{total_score}/6"
        if isinstance(ema20, (int, float)) and isinstance(max_pain, (int, float))
        else f"📊 *Market Validation*\n  Score: +{total_score}/6"
    )

    return {
        'approved':     not blocked,
        'blocked':      blocked,
        'block_reason': block_reason,
        'score_boost':  total_score,
        'vix':          vix_check,
        'oi':           oi_check,
        'ema':          ema_check,
        'reasons':      reasons,
        'summary':      summary,
    }


# ─────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("Testing Market Validator...")
    print()

    result = validate_trade('BULLISH', 57500)

    print(f"Approved: {result['approved']}")
    print(f"Score boost: +{result['score_boost']}/6")
    print()
    for r in result['reasons']:
        print(f"  {r}")

    if result['blocked']:
        print(f"\n🚫 BLOCKED: {result['block_reason']}")
