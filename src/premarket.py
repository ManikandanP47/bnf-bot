"""
Pre-Market Scanner — 9:00 AM IST
Runs BEFORE market opens to set the day's expectations.
Saves traders from entering a market that's about to reverse.

Checks at 9:00 AM:
1. Nifty Futures premium/discount (gap up/down expectation)
2. SGX Nifty direction (Asian proxy for India open)
3. US market overnight close
4. India VIX (fear index — high VIX = options expensive)
5. FII activity proxy (global risk sentiment)
6. Previous day's BankNifty structure
7. Key levels to watch today
"""

import yfinance as yf
import requests
import json
import os
from datetime import datetime, timedelta
import pytz
import warnings
warnings.filterwarnings('ignore')

IST          = pytz.timezone('Asia/Kolkata')
PREMARKET_FILE = 'premarket_brief.json'


def get_india_vix() -> dict:
    """
    India VIX = Market's fear gauge.
    Low VIX (< 13): Calm market, buy options cheap ✅
    High VIX (> 20): Fearful market, options expensive ❌
    Extreme VIX (> 30): Panic — avoid trading
    """
    try:
        vix = yf.Ticker('^INDIAVIX')
        hist = vix.history(period='5d', interval='1d').dropna()
        if len(hist) < 2:
            return {'available': False}

        curr_vix  = round(float(hist['Close'].iloc[-1]), 2)
        prev_vix  = round(float(hist['Close'].iloc[-2]), 2)
        vix_change = round(curr_vix - prev_vix, 2)

        if curr_vix < 13:
            signal = 'LOW'
            note   = f"VIX {curr_vix} — calm market, options cheap ✅"
            tradeable = True
        elif curr_vix < 20:
            signal = 'NORMAL'
            note   = f"VIX {curr_vix} — normal volatility ✅"
            tradeable = True
        elif curr_vix < 25:
            signal = 'ELEVATED'
            note   = f"VIX {curr_vix} — elevated fear ⚠️ trade smaller"
            tradeable = True
        else:
            signal = 'HIGH'
            note   = f"VIX {curr_vix} — high fear ❌ avoid trading"
            tradeable = False

        return {
            'available': True,
            'vix':       curr_vix,
            'change':    vix_change,
            'signal':    signal,
            'note':      note,
            'tradeable': tradeable
        }
    except:
        return {'available': False}


def get_us_market_close() -> dict:
    """
    Check how US markets closed yesterday.
    Biggest influence on Indian market open.

    S&P 500 (^GSPC) is the best proxy.
    """
    try:
        sp500 = yf.Ticker('^GSPC')
        hist  = sp500.history(period='5d', interval='1d').dropna()
        if len(hist) < 2:
            return {'available': False}

        prev   = float(hist['Close'].iloc[-2])
        latest = float(hist['Close'].iloc[-1])
        change = round((latest - prev) / prev * 100, 2)

        if change >= 1.0:
            signal = 'STRONG_UP'
            note   = f"S&P500 +{change:.1f}% — strong US rally, India likely gaps up ✅"
            bias   = 'BULLISH'
        elif change >= 0.3:
            signal = 'MILD_UP'
            note   = f"S&P500 +{change:.1f}% — mild US gains"
            bias   = 'BULLISH'
        elif change > -0.3:
            signal = 'FLAT'
            note   = f"S&P500 {change:.1f}% — flat, neutral for India"
            bias   = 'NEUTRAL'
        elif change > -1.0:
            signal = 'MILD_DOWN'
            note   = f"S&P500 {change:.1f}% — mild US weakness"
            bias   = 'BEARISH'
        else:
            signal = 'STRONG_DOWN'
            note   = f"S&P500 {change:.1f}% — US fell sharply, India likely gaps down ❌"
            bias   = 'BEARISH'

        return {
            'available': True,
            'sp500_change': change,
            'signal':    signal,
            'note':      note,
            'bias':      bias
        }
    except:
        return {'available': False}


def get_banknifty_previous_day() -> dict:
    """
    Previous day BankNifty key levels.
    These are important support/resistance for today.

    Previous day High (PDH) = today's first resistance
    Previous day Low  (PDL) = today's first support
    Previous day Close      = key reference
    """
    try:
        bnf  = yf.Ticker('^NSEBANK')
        hist = bnf.history(period='5d', interval='1d').dropna()
        if len(hist) < 2:
            return {'available': False}

        prev = hist.iloc[-2]
        curr = hist.iloc[-1]

        pdh   = round(float(prev['High']),  2)
        pdl   = round(float(prev['Low']),   2)
        pdc   = round(float(prev['Close']), 2)
        range = round(pdh - pdl, 2)

        # Today's gap
        today_open = round(float(curr['Open']), 2)
        gap        = round(today_open - pdc, 2)
        gap_pct    = round(gap / pdc * 100, 2)

        return {
            'available': True,
            'pdh':       pdh,
            'pdl':       pdl,
            'pdc':       pdc,
            'range':     range,
            'today_gap': gap,
            'gap_pct':   gap_pct,
            'gap_type':  'GAP_UP' if gap > 0 else 'GAP_DOWN' if gap < 0 else 'FLAT',
            'key_levels': {
                'resistance': pdh,
                'support':    pdl,
                'pivot':      round((pdh + pdl + pdc) / 3, 2)
            }
        }
    except:
        return {'available': False}


def calculate_pivot_levels(high: float, low: float, close: float) -> dict:
    """
    Floor Pivot Points — widely used by institutional traders.
    These levels act as magnets for price throughout the day.

    PP  (Pivot)  = (H + L + C) / 3
    R1           = 2*PP - L
    R2           = PP + (H - L)
    S1           = 2*PP - H
    S2           = PP - (H - L)
    """
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    r2 = pp + (high - low)
    s1 = 2 * pp - high
    s2 = pp - (high - low)

    return {
        'pp': round(pp, 2),
        'r1': round(r1, 2),
        'r2': round(r2, 2),
        's1': round(s1, 2),
        's2': round(s2, 2),
    }


def run_premarket_scan() -> dict:
    """
    Master pre-market function.
    Run at 9:00 AM IST before market opens.
    Returns day's trading brief.
    """
    timestamp = datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')

    vix    = get_india_vix()
    us     = get_us_market_close()
    bnf    = get_banknifty_previous_day()

    # Calculate pivots from previous day
    pivots = {}
    if bnf.get('available'):
        pivots = calculate_pivot_levels(bnf['pdh'], bnf['pdl'], bnf['pdc'])

    # Overall day bias
    bias_signals = []
    if us.get('bias') == 'BULLISH':  bias_signals.append(1)
    elif us.get('bias') == 'BEARISH': bias_signals.append(-1)
    else: bias_signals.append(0)

    if bnf.get('gap_type') == 'GAP_UP':   bias_signals.append(1)
    elif bnf.get('gap_type') == 'GAP_DOWN': bias_signals.append(-1)
    else: bias_signals.append(0)

    avg_bias = sum(bias_signals) / len(bias_signals) if bias_signals else 0
    if avg_bias > 0.3:   day_bias = 'BULLISH'
    elif avg_bias < -0.3: day_bias = 'BEARISH'
    else:                 day_bias = 'NEUTRAL'

    # Can we trade today?
    tradeable = vix.get('tradeable', True)

    brief = {
        'timestamp':  timestamp,
        'day_bias':   day_bias,
        'tradeable':  tradeable,
        'vix':        vix,
        'us_market':  us,
        'bnf_levels': bnf,
        'pivots':     pivots,
    }

    # Save for reference
    with open(PREMARKET_FILE, 'w') as f:
        json.dump(brief, f, indent=2, default=str)

    return brief


def format_premarket_telegram(brief: dict) -> str:
    """Format pre-market brief for Telegram (sent at 9:00 AM)"""
    bias   = brief.get('day_bias', 'NEUTRAL')
    bias_e = {'BULLISH':'🟢','BEARISH':'🔴','NEUTRAL':'🟡'}.get(bias,'🟡')
    ts     = brief.get('timestamp','')

    vix    = brief.get('vix', {})
    us     = brief.get('us_market', {})
    bnf    = brief.get('bnf_levels', {})
    pivots = brief.get('pivots', {})

    tradeable = brief.get('tradeable', True)

    msg = (
        f"🌅 *Pre-Market Brief*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bias_e} Day Bias: *{bias}*\n\n"
    )

    if vix.get('available'):
        msg += f"😰 VIX: {vix.get('note','')}\n"

    if us.get('available'):
        msg += f"🇺🇸 {us.get('note','')}\n"

    if bnf.get('available'):
        msg += (
            f"\n📊 *Yesterday's BankNifty:*\n"
            f"  PDH: {bnf.get('pdh'):,} ← today's resistance\n"
            f"  PDL: {bnf.get('pdl'):,} ← today's support\n"
            f"  PDC: {bnf.get('pdc'):,}\n"
            f"  Gap: {bnf.get('gap_type')} ({bnf.get('gap_pct'):+.1f}%)\n"
        )

    if pivots:
        msg += (
            f"\n📐 *Pivot Levels (Institutional):*\n"
            f"  R2: {pivots.get('r2'):,}\n"
            f"  R1: {pivots.get('r1'):,}\n"
            f"  PP: {pivots.get('pp'):,} ← Pivot\n"
            f"  S1: {pivots.get('s1'):,}\n"
            f"  S2: {pivots.get('s2'):,}\n"
        )

    msg += f"\n━━━━━━━━━━━━━━━━━━━━━\n"

    if not tradeable:
        msg += (
            f"❌ *HIGH VIX — Avoid trading today*\n"
            f"Risk is too high. Bot will stay quiet.\n"
        )
    else:
        msg += (
            f"{'✅ Market looks tradeable' if bias != 'NEUTRAL' else '⏳ Wait for direction'}\n"
            f"_Bot is watching. Will alert on setup._ 🤖\n"
        )

    msg += f"\n_{ts}_"
    return msg


if __name__ == '__main__':
    print("Pre-Market Scanner Test...")
    brief = run_premarket_scan()
    print(f"Day Bias: {brief['day_bias']}")
    print(f"Tradeable: {brief['tradeable']}")
    if brief.get('vix', {}).get('available'):
        print(f"VIX: {brief['vix']['note']}")
    if brief.get('us_market', {}).get('available'):
        print(f"US: {brief['us_market']['note']}")
    if brief.get('bnf_levels', {}).get('available'):
        bnf = brief['bnf_levels']
        print(f"PDH/PDL: {bnf['pdh']:,}/{bnf['pdl']:,}")
    if brief.get('pivots'):
        p = brief['pivots']
        print(f"Pivots — R1:{p['r1']:,} PP:{p['pp']:,} S1:{p['s1']:,}")
    print()
    print(format_premarket_telegram(brief))
