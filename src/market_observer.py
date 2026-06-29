"""
Market Observer — Continuous Market Intelligence
Runs inside every 15-min check but analyses 5-min candles.
Builds market context, session awareness, and pattern memory.

The bot OBSERVES continuously and ACTS only on perfect setups.
Like a sniper — watches all day, shoots once perfectly.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, time as dtime
import pytz
import warnings
warnings.filterwarnings('ignore')

IST            = pytz.timezone('Asia/Kolkata')
SYMBOL         = '^NSEBANK'
DAILY_OBS_FILE = 'daily_observations.json'


# ─── MARKET SESSIONS ─────────────────────────────────────────────
# BankNifty behaves VERY differently in each session
# Based on real institutional behaviour patterns

SESSIONS = {
    'OPEN_VOLATILE':  (dtime(9, 15), dtime(9, 45)),   # First 30 min — chaotic
    'MORNING_TREND':  (dtime(9, 45), dtime(11, 30)),  # Best trending window ✅
    'LUNCH_CHOP':     (dtime(11, 30), dtime(13, 0)),  # Low volume, choppy ❌
    'AFTERNOON_MOVE': (dtime(13, 0),  dtime(14, 30)), # Second opportunity ✅
    'EOD_CHOP':       (dtime(14, 30), dtime(15, 30)), # Too late, avoid ❌
}


def get_current_session() -> dict:
    """Returns which market session we are in right now"""
    now = datetime.now(IST).time()
    for name, (start, end) in SESSIONS.items():
        if start <= now < end:
            quality = 'GOOD' if name in ['MORNING_TREND', 'AFTERNOON_MOVE'] else 'AVOID'
            return {
                'session':  name,
                'quality':  quality,
                'tradeable': quality == 'GOOD'
            }
    return {'session': 'CLOSED', 'quality': 'CLOSED', 'tradeable': False}


def get_5min_data() -> pd.DataFrame:
    """Fetch 5-min BankNifty data for today"""
    try:
        df = yf.Ticker(SYMBOL).history(period='2d', interval='5m')
        return df.dropna() if len(df) > 5 else None
    except:
        return None


def calculate_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI — momentum indicator. >70 = overbought, <30 = oversold"""
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)


def calculate_macd(series: pd.Series) -> dict:
    """MACD — trend direction and momentum"""
    ema12   = series.ewm(span=12).mean()
    ema26   = series.ewm(span=26).mean()
    macd    = ema12 - ema26
    signal  = macd.ewm(span=9).mean()
    hist    = macd - signal

    return {
        'macd':    round(float(macd.iloc[-1]), 2),
        'signal':  round(float(signal.iloc[-1]), 2),
        'hist':    round(float(hist.iloc[-1]), 2),
        'bullish': float(hist.iloc[-1]) > float(hist.iloc[-2])
    }


def detect_opening_range() -> dict:
    """
    Opening Range = High and Low of first 15 minutes (9:15-9:30)
    Most powerful level of the day.

    If price breaks ABOVE opening range high → BULLISH day
    If price breaks BELOW opening range low → BEARISH day
    """
    df = get_5min_data()
    if df is None:
        return {'available': False}

    today  = datetime.now(IST).date()
    # Filter today's candles only
    today_df = df[df.index.date == today]

    if len(today_df) < 3:
        return {'available': False, 'reason': 'Market just opened'}

    # First 3 candles = 9:15, 9:20, 9:25 = opening 15 min
    opening = today_df.head(3)
    or_high = round(float(opening['High'].max()), 2)
    or_low  = round(float(opening['Low'].min()), 2)
    current = round(float(today_df['Close'].iloc[-1]), 2)

    # Where is current price relative to opening range?
    if current > or_high:
        position = 'ABOVE'
        signal   = 'BULLISH'
        note     = f"Price {current:,} broke above OR high {or_high:,} ✅"
    elif current < or_low:
        position = 'BELOW'
        signal   = 'BEARISH'
        note     = f"Price {current:,} broke below OR low {or_low:,} 🔴"
    else:
        position = 'INSIDE'
        signal   = 'NEUTRAL'
        note     = f"Price {current:,} inside OR ({or_low:,}-{or_high:,})"

    return {
        'available': True,
        'or_high':   or_high,
        'or_low':    or_low,
        'current':   current,
        'position':  position,
        'signal':    signal,
        'note':      note
    }


def detect_market_regime(df_5m: pd.DataFrame) -> dict:
    """
    Is BankNifty TRENDING or RANGING today?

    TRENDING: Strong directional move, OBs reliable ✅
    RANGING:  Bouncing sideways, OBs less reliable ⚠️

    How to detect:
    - ADX > 25 = trending
    - High/Low range expanding = trending
    - Price making HHs or LLs = trending
    """
    if df_5m is None or len(df_5m) < 20:
        return {'regime': 'UNKNOWN', 'trend_strength': 0, 'direction': 'UNKNOWN', 'range_pct': 0, 'efficiency': 0, 'rsi': 50, 'note': 'Insufficient data', 'tradeable': False}

    # Use today's candles only
    today = datetime.now(IST).date()
    today_df = df_5m[df_5m.index.date == today]

    if len(today_df) < 10:
        return {'regime': 'EARLY', 'trend_strength': 0, 'direction': 'UNKNOWN', 'range_pct': 0, 'efficiency': 0, 'rsi': 50, 'note': 'Market just opened', 'tradeable': False}

    closes  = today_df['Close']
    highs   = today_df['High']
    lows    = today_df['Low']

    # Range as % of price
    day_range     = float(highs.max() - lows.min())
    avg_price     = float(closes.mean())
    range_pct     = day_range / avg_price * 100

    # Directional movement
    first_price   = float(closes.iloc[0])
    last_price    = float(closes.iloc[-1])
    net_move      = abs(last_price - first_price)
    net_move_pct  = net_move / first_price * 100

    # Trend efficiency (how much of range is directional)
    efficiency    = net_move_pct / range_pct if range_pct > 0 else 0

    # RSI for momentum
    rsi = calculate_rsi(closes, 9)

    # Determine regime
    if efficiency > 0.5 and range_pct > 0.3:
        regime         = 'TRENDING'
        trend_strength = round(efficiency * 100, 0)
        direction      = 'UP' if last_price > first_price else 'DOWN'
        note           = f"Strong {'up' if direction=='UP' else 'down'}trend (efficiency {efficiency:.0%})"
    elif range_pct < 0.2:
        regime         = 'TIGHT_RANGE'
        trend_strength = 0
        direction      = 'NEUTRAL'
        note           = f"Very tight range ({range_pct:.1f}%) — avoid trading"
    else:
        regime         = 'RANGING'
        trend_strength = round(efficiency * 50, 0)
        direction      = 'NEUTRAL'
        note           = f"Choppy market (efficiency {efficiency:.0%}) — wait for breakout"

    return {
        'regime':         regime,
        'direction':      direction,
        'trend_strength': trend_strength,
        'range_pct':      round(range_pct, 2),
        'efficiency':     round(efficiency, 2),
        'rsi':            rsi,
        'note':           note,
        'tradeable':      regime == 'TRENDING'
    }


def observe_market() -> dict:
    """
    Master observation function.
    Called every 15-min run. Builds rich market context.
    Returns full picture of what market is doing RIGHT NOW.
    """
    now       = datetime.now(IST)
    timestamp = now.strftime('%d %b %Y %I:%M %p IST')

    # Fetch 5-min data once (used by all functions)
    df_5m = get_5min_data()
    if df_5m is None:
        return {
            'timestamp':    timestamp,
            'data':         False,
            'tradeable':    False,
            'reason':       'Market data unavailable'
        }

    # Filter today only
    today    = now.date()
    df_today = df_5m[df_5m.index.date == today] if len(df_5m) > 0 else df_5m

    current  = round(float(df_5m['Close'].iloc[-1]), 2)

    # All observations
    session  = get_current_session()
    or_data  = detect_opening_range()
    regime   = detect_market_regime(df_5m)

    # RSI and MACD on 5-min
    rsi  = calculate_rsi(df_5m['Close']) if len(df_5m) >= 14 else 50
    macd = calculate_macd(df_5m['Close']) if len(df_5m) >= 26 else {'bullish': True}

    # Volume profile
    avg_vol    = float(df_today['Volume'].mean()) if len(df_today) > 5 else 0
    curr_vol   = float(df_today['Volume'].iloc[-1]) if len(df_today) > 0 else 0
    vol_ratio  = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0

    # Build observation
    obs = {
        'timestamp': timestamp,
        'current':   current,
        'session':   session,
        'or':        or_data,
        'regime':    regime,
        'rsi':       rsi,
        'macd':      macd,
        'volume':    {
            'current': curr_vol,
            'avg':     round(avg_vol, 0),
            'ratio':   vol_ratio,
            'high':    vol_ratio > 1.5
        }
    }

    # Overall tradeable decision
    tradeable_reasons = []
    not_tradeable     = []

    if not session['tradeable']:
        not_tradeable.append(f"Session: {session['session']} — avoid")

    if or_data.get('available'):
        tradeable_reasons.append(f"OR: {or_data['note']}")

    if regime['tradeable']:
        tradeable_reasons.append(f"Regime: {regime['note']}")
    else:
        not_tradeable.append(f"Market: {regime['note']}")

    obs['overall'] = {
        'tradeable':     len(not_tradeable) == 0,
        'confidence':    max(0, 50 + len(tradeable_reasons)*15 - len(not_tradeable)*20),
        'go_reasons':    tradeable_reasons,
        'no_reasons':    not_tradeable
    }

    # Save observation to daily log
    _save_observation(obs)

    return obs


def _save_observation(obs: dict):
    """Save market observation to daily log — builds pattern library"""
    try:
        log = []
        if os.path.exists(DAILY_OBS_FILE):
            with open(DAILY_OBS_FILE) as f:
                log = json.load(f)

        # Keep only last 5 days of observations
        today = datetime.now(IST).strftime('%d %b %Y')
        log   = [o for o in log
                 if o.get('timestamp','').startswith(today)]

        # Compact version for storage
        compact = {
            'timestamp': obs['timestamp'],
            'price':     obs['current'],
            'session':   obs['session']['session'],
            'regime':    obs['regime']['regime'],
            'rsi':       obs['rsi'],
            'tradeable': obs['overall']['tradeable'],
            'or_signal': obs['or'].get('signal', 'N/A')
        }
        log.append(compact)

        with open(DAILY_OBS_FILE, 'w') as f:
            json.dump(log[-200:], f, indent=2, default=str)  # Keep last 200

    except Exception as e:
        pass


def get_market_summary() -> str:
    """Human-readable market summary for Telegram"""
    obs = observe_market()
    if not obs.get('session'):
        return "Market data unavailable"

    current   = obs.get('current', 0)
    session   = obs['session']
    regime    = obs['regime']
    or_data   = obs.get('or', {})
    rsi       = obs.get('rsi', 50)
    overall   = obs.get('overall', {})

    sess_emoji = '✅' if session['tradeable'] else '⏳'
    reg_emoji  = '📈' if regime['tradeable'] else '📊'

    summary = (
        f"📊 *Market Context*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Price: *{current:,}*\n\n"
        f"{sess_emoji} Session: {session['session']}\n"
        f"{reg_emoji} Regime:  {regime['regime']} ({regime.get('direction','')})\n"
        f"📉 RSI: {rsi} "
        f"({'Overbought' if rsi>70 else 'Oversold' if rsi<30 else 'Neutral'})\n"
    )

    if or_data.get('available'):
        summary += f"🔲 Opening Range: {or_data.get('note','')}\n"

    summary += f"\n"
    if overall.get('tradeable'):
        summary += "✅ *MARKET READY TO TRADE*\n"
    else:
        summary += "⏳ *WAITING FOR BETTER CONDITIONS*\n"
        for r in overall.get('no_reasons', []):
            summary += f"  • {r}\n"

    return summary


if __name__ == '__main__':
    print("Market Observer — Live Test")
    print("="*50)
    obs = observe_market()
    print(f"Time:    {obs['timestamp']}")
    print(f"Price:   {obs.get('current', 0):,}")
    print(f"Session: {obs['session']['session']} ({obs['session']['quality']})")
    print(f"Regime:  {obs['regime']['regime']}")
    print(f"RSI:     {obs.get('rsi', 0)}")
    if obs.get('or', {}).get('available'):
        print(f"OR:      {obs['or']['note']}")
    print()
    print(get_market_summary())
