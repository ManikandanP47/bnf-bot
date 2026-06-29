"""
BankNifty SMC Scanner — Clean Version
Finds: Order Blocks, FVGs, BOS, CHoCH on BankNifty
Recommends: CE or PE with strike, expiry, premium
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import warnings
warnings.filterwarnings('ignore')

IST      = pytz.timezone('Asia/Kolkata')
SYMBOL   = '^NSEBANK'
LOT_SIZE = 15
STRIKE_GAP = 100


def get_data(interval='1d', period='6mo'):
    try:
        df = yf.Ticker(SYMBOL).history(period=period, interval=interval)
        return df.dropna() if len(df) > 10 else None
    except:
        return None


def swing_points(df, n=3):
    highs, lows = [], []
    for i in range(n, len(df) - n):
        if all(df['High'].iloc[i] >= df['High'].iloc[i-j] for j in range(1,n+1)) and \
           all(df['High'].iloc[i] >= df['High'].iloc[i+j] for j in range(1,n+1)):
            highs.append({'price': df['High'].iloc[i], 'idx': i})
        if all(df['Low'].iloc[i] <= df['Low'].iloc[i-j] for j in range(1,n+1)) and \
           all(df['Low'].iloc[i] <= df['Low'].iloc[i+j] for j in range(1,n+1)):
            lows.append({'price': df['Low'].iloc[i], 'idx': i})
    return highs, lows


def market_structure(df):
    highs, lows = swing_points(df)
    if len(highs) < 2 or len(lows) < 2:
        return 'NEUTRAL', 'No clear structure'

    hh = highs[-1]['price'] > highs[-2]['price']
    hl = lows[-1]['price']  > lows[-2]['price']
    lh = highs[-1]['price'] < highs[-2]['price']
    ll = lows[-1]['price']  < lows[-2]['price']

    if hh and hl: return 'BULLISH', f"HH {highs[-1]['price']:,.0f} + HL {lows[-1]['price']:,.0f}"
    if lh and ll: return 'BEARISH', f"LH {highs[-1]['price']:,.0f} + LL {lows[-1]['price']:,.0f}"
    return 'NEUTRAL', 'Mixed — wait for clarity'


def order_blocks(df, trend):
    obs = []
    for i in range(5, len(df)-2):
        up = (df['High'].iloc[i+1:i+4].max() - df['High'].iloc[i]) / df['High'].iloc[i]
        dn = (df['Low'].iloc[i] - df['Low'].iloc[i+1:i+4].min()) / df['Low'].iloc[i]

        if trend in ['BULLISH','NEUTRAL'] and df['Close'].iloc[i] < df['Open'].iloc[i] and up >= 0.008:
            obs.append({'type':'BUY', 'high': round(df['High'].iloc[i],2),
                        'low': round(df['Low'].iloc[i],2),
                        'mid': round((df['High'].iloc[i]+df['Low'].iloc[i])/2,2)})

        if trend in ['BEARISH','NEUTRAL'] and df['Close'].iloc[i] > df['Open'].iloc[i] and dn >= 0.008:
            obs.append({'type':'SELL', 'high': round(df['High'].iloc[i],2),
                        'low': round(df['Low'].iloc[i],2),
                        'mid': round((df['High'].iloc[i]+df['Low'].iloc[i])/2,2)})
    return obs[-6:]


def fvg(df, trend):
    gaps = []
    for i in range(2, len(df)):
        if trend in ['BULLISH','NEUTRAL']:
            if df['Low'].iloc[i] > df['High'].iloc[i-2]:
                gaps.append({'type':'BUY',
                             'bottom': round(df['High'].iloc[i-2],2),
                             'top':    round(df['Low'].iloc[i],2),
                             'mid':    round((df['High'].iloc[i-2]+df['Low'].iloc[i])/2,2)})
        if trend in ['BEARISH','NEUTRAL']:
            if df['Low'].iloc[i-2] > df['High'].iloc[i]:
                gaps.append({'type':'SELL',
                             'top':    round(df['Low'].iloc[i-2],2),
                             'bottom': round(df['High'].iloc[i],2),
                             'mid':    round((df['Low'].iloc[i-2]+df['High'].iloc[i])/2,2)})
    return gaps[-6:]


def liquidity_grab(df, trend):
    """Detect if price just swept liquidity (equal highs/lows)"""
    recent = df.tail(5)
    highs  = df['High'].iloc[-20:-5]
    lows   = df['Low'].iloc[-20:-5]

    swept_high = any(abs(recent['High'].max() - h) / h < 0.003 for h in highs)
    swept_low  = any(abs(recent['Low'].min() - l) / l < 0.003 for l in lows)

    if trend == 'BULLISH' and swept_low:
        return True, "Sell-side liquidity swept below equal lows"
    if trend == 'BEARISH' and swept_high:
        return True, "Buy-side liquidity swept above equal highs"
    return False, ""


def check_1h(trend, key_level):
    """1H chart confirmation near key level"""
    df = get_data('1h', '5d')
    if df is None: return False, "No 1H data"

    current = float(df['Close'].iloc[-1])
    near    = abs(current - key_level) / key_level < 0.006

    if not near:
        return False, f"Price {current:,.0f} not near {key_level:,.0f} yet"

    # CHoCH on 1H
    for i in range(2, len(df.tail(10))):
        r = df.tail(10)
        if trend == 'BULLISH' and r['Close'].iloc[i] > r['High'].iloc[i-2]:
            return True, "1H CHoCH confirmed (bullish flip)"
        if trend == 'BEARISH' and r['Close'].iloc[i] < r['Low'].iloc[i-2]:
            return True, "1H CHoCH confirmed (bearish flip)"

    return True, "Price at level — watch for 1H confirmation"


def next_expiry():
    from src.expiry_picker import next_banknifty_expiry
    return next_banknifty_expiry()


def strike_and_premium(current, trend):
    atm  = round(current / STRIKE_GAP) * STRIKE_GAP
    if trend == 'BULLISH':
        strike   = atm + STRIKE_GAP
        opt_type = 'CE'
        dist     = strike - current
    else:
        strike   = atm - STRIKE_GAP
        opt_type = 'PE'
        dist     = current - strike

    dist_pct = dist / current * 100
    if   dist_pct < 0.5: base = 265
    elif dist_pct < 1.0: base = 200
    else:                base = 140

    premium    = base
    sl_prem    = round(premium * 0.70, 0)
    tgt_prem   = premium * 2
    lot_cost   = premium * LOT_SIZE
    max_loss   = round(premium * 0.30 * LOT_SIZE, 0)
    max_profit = round(premium * LOT_SIZE, 0)

    return {
        'strike':     strike,
        'opt_type':   opt_type,
        'name':       f"BANKNIFTY {strike} {opt_type}",
        'premium':    premium,
        'sl_prem':    sl_prem,
        'tgt_prem':   tgt_prem,
        'lot_cost':   lot_cost,
        'max_loss':   max_loss,
        'max_profit': max_profit,
        'expiry':     next_expiry()
    }


def analyse():
    """
    Full BankNifty analysis — called by bot and GitHub Actions
    Returns clean dict with everything needed for the Telegram message
    """
    now = datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')

    # Fetch daily data
    df = get_data()
    if df is None:
        return {'setup': False, 'reason': 'Data unavailable', 'time': now}

    current = float(df['Close'].iloc[-1])
    trend, struct_desc = market_structure(df)

    if trend == 'NEUTRAL':
        return {
            'setup':   False,
            'trend':   trend,
            'current': current,
            'reason':  f"Market neutral — {struct_desc}",
            'time':    now
        }

    obs  = order_blocks(df, trend)
    fvgs = fvg(df, trend)
    liq_grabbed, liq_reason = liquidity_grab(df, trend)

    score   = 2  # Base score for having a trend
    reasons = [f"✅ {trend} structure: {struct_desc}"]
    setup_level = None

    # Check OB
    ob_type = 'BUY' if trend == 'BULLISH' else 'SELL'
    for ob in reversed(obs):
        if ob['type'] == ob_type and ob['low'] <= current <= ob['high'] * 1.01:
            score += 3
            reasons.append(f"✅ Price at {trend} Order Block: {ob['low']:,.0f}–{ob['high']:,.0f}")
            setup_level = ob['mid']
            break

    # Check FVG
    fvg_type = 'BUY' if trend == 'BULLISH' else 'SELL'
    for g in reversed(fvgs):
        if g['type'] == fvg_type and g['bottom'] <= current <= g['top'] * 1.01:
            score += 2
            reasons.append(f"✅ Price in {trend} FVG: {g['bottom']:,.0f}–{g['top']:,.0f}")
            if not setup_level:
                setup_level = g['mid']
            break

    # Liquidity grab
    if liq_grabbed:
        score += 2
        reasons.append(f"✅ Liquidity grab: {liq_reason}")

    # 1H confirmation
    if setup_level:
        confirmed, h1_reason = check_1h(trend, setup_level)
        if confirmed:
            score += 2
            reasons.append(f"✅ {h1_reason}")
        else:
            reasons.append(f"⚠️ {h1_reason}")

    # No key level found
    if not setup_level:
        # Show nearest OBs as reference
        near_obs = [o for o in obs if o['type'] == ob_type]
        if near_obs:
            closest = min(near_obs, key=lambda x: abs(x['mid'] - current))
            reasons.append(f"📌 Nearest OB: {closest['low']:,.0f}–{closest['high']:,.0f}")
        reasons.append(f"⚠️ Price not at key zone — wait for pullback")
        return {
            'setup':   False,
            'trend':   trend,
            'current': round(current, 2),
            'reasons': reasons,
            'score':   score,
            'time':    now
        }

    # Minimum score check
    if score < 5:
        reasons.append("⚠️ Setup forming but not confirmed yet")
        return {
            'setup':   False,
            'trend':   trend,
            'current': round(current, 2),
            'reasons': reasons,
            'score':   score,
            'time':    now
        }

    # ── Market Validator (VIX + OI + EMA) ────────────────────────
    try:
        from src.market_validator import validate_trade
        validation = validate_trade(trend, current)
        score     += validation['score_boost']
        for r in validation['reasons']:
            if r:
                reasons.append(r)
        if validation.get('blocked'):
            reasons.append(f"🚫 Validator blocked: {validation['block_reason']}")
            return {
                'setup':   False,
                'trend':   trend,
                'current': round(current, 2),
                'reasons': reasons,
                'score':   score,
                'time':    now
            }
    except Exception as e:
        reasons.append(f"⚠️ Validator unavailable: {str(e)[:30]}")

    # Build trade
    trade = strike_and_premium(current, trend)

    return {
        'setup':       True,
        'trend':       trend,
        'current':     round(current, 2),
        'score':       score,
        'reasons':     reasons,
        'time':        now,
        **trade
    }


if __name__ == '__main__':
    r = analyse()
    print(f"BankNifty: {r['trend']} | Setup: {r['setup']} | Score: {r.get('score',0)}")
    for reason in r.get('reasons', []):
        print(f"  {reason}")
    if r.get('setup'):
        print(f"\nTrade: {r.get('name')} | Expiry: {r.get('expiry')}")
        print(f"Premium: ~Rs {r.get('premium')} | SL: Rs {r.get('sl_prem')} | Target: Rs {r.get('tgt_prem')}")
        print(f"Max Loss: Rs {r.get('max_loss')} | Max Profit: Rs {r.get('max_profit')}")
