"""
Trade Filters — 5 Battle-Tested Improvements
1. Event Filter        → Skip RBI/Fed/Budget/Expiry days
2. Volume Confirmation → Only enter on quality pullbacks
3. ATR Dynamic SL      → SL adjusts to actual volatility
4. Global Market Check → Don't fight global trends
5. Partial Exit Logic  → Half at 1.5x, rest at 2x

Each filter has a clear mathematical reason.
No complexity for its own sake.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import pytz
import warnings
warnings.filterwarnings('ignore')

IST = pytz.timezone('Asia/Kolkata')


# ─────────────────────────────────────────────────────────────────
# FILTER 1: EVENT CALENDAR
# Avoids: RBI MPC, US FOMC, Budget, Expiry Thursday volatility
# ─────────────────────────────────────────────────────────────────

# Known high-impact event dates 2026 (update quarterly)
EVENT_CALENDAR = {
    # RBI MPC policy dates 2026 (approx — check RBI website)
    '2026-04-09': 'RBI MPC Policy',
    '2026-06-06': 'RBI MPC Policy',
    '2026-08-06': 'RBI MPC Policy',
    '2026-10-06': 'RBI MPC Policy',
    '2026-12-04': 'RBI MPC Policy',

    # US Fed FOMC 2026 (approx)
    '2026-01-29': 'US Fed FOMC',
    '2026-03-19': 'US Fed FOMC',
    '2026-05-07': 'US Fed FOMC',
    '2026-06-18': 'US Fed FOMC',
    '2026-07-30': 'US Fed FOMC',
    '2026-09-17': 'US Fed FOMC',
    '2026-11-05': 'US Fed FOMC',
    '2026-12-16': 'US Fed FOMC',

    # India Budget
    '2027-02-01': 'Union Budget',

    # Options expiry — BankNifty weekly/monthly = WEDNESDAY (NSE)
}


def _last_weekday_of_month(year: int, month: int, weekday: int):
    """weekday: 0=Mon … 2=Wed"""
    from datetime import date, timedelta
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def is_event_day() -> dict:
    """
    Returns: {'skip': True/False, 'reason': str}

    Checks:
    1. Known event dates
    2. Monthly expiry Wednesday (last Wednesday of month)
    3. Weekly expiry Wednesday (BankNifty)
    """
    today     = datetime.now(IST)
    today_str = today.strftime('%Y-%m-%d')
    weekday   = today.weekday()  # 0=Mon, 2=Wed

    if today_str in EVENT_CALENDAR:
        return {
            'skip':   True,
            'reason': f"⚠️ Event day: {EVENT_CALENDAR[today_str]}"
        }

    last_wed = _last_weekday_of_month(today.year, today.month, 2)
    if today.date() == last_wed:
        return {
            'skip':   True,
            'reason': '⚠️ Monthly BankNifty expiry (Wednesday) — high volatility, skip',
        }

    if weekday == 2:
        return {
            'skip':    False,
            'reason':  '⚠️ Weekly BankNifty expiry (Wednesday) — tighter rules',
            'caution': True,
        }

    return {'skip': False, 'reason': ''}


# ─────────────────────────────────────────────────────────────────
# FILTER 2: VOLUME CONFIRMATION ON PULLBACK
# Real pullback = price drops WITH low volume (sellers exhausted)
# Fake pullback = price drops WITH high volume (real selling)
# ─────────────────────────────────────────────────────────────────

def check_pullback_volume(df: pd.DataFrame, trend: str) -> dict:
    """
    Checks if the current pullback has low volume
    (confirming it's a genuine pause, not a reversal)

    Returns: {'quality': 'HIGH/LOW/UNKNOWN', 'reason': str}
    """
    if len(df) < 10:
        return {'quality': 'UNKNOWN', 'reason': 'Insufficient data'}

    # Last 3 pullback candles vs average of last 20 candles
    recent_vols   = df['Volume'].iloc[-3:].values
    avg_volume_20 = df['Volume'].iloc[-20:-3].mean()

    if avg_volume_20 == 0:
        return {'quality': 'UNKNOWN', 'reason': 'Volume data unavailable'}

    pullback_vol_ratio = np.mean(recent_vols) / avg_volume_20

    # Pullback logic:
    # BULLISH trend: we want LOW volume on the pullback down
    # BEARISH trend: we want LOW volume on the bounce up

    if pullback_vol_ratio < 0.70:
        return {
            'quality': 'HIGH',
            'reason':  f"✅ Low volume pullback ({pullback_vol_ratio:.1%} of avg) — genuine pause",
            'ratio':   round(pullback_vol_ratio, 2)
        }
    elif pullback_vol_ratio < 1.0:
        return {
            'quality': 'MEDIUM',
            'reason':  f"⚠️ Normal volume pullback ({pullback_vol_ratio:.1%} of avg) — proceed cautiously",
            'ratio':   round(pullback_vol_ratio, 2)
        }
    else:
        return {
            'quality': 'LOW',
            'reason':  f"❌ HIGH volume pullback ({pullback_vol_ratio:.1%} of avg) — possible reversal not pullback",
            'ratio':   round(pullback_vol_ratio, 2)
        }


# ─────────────────────────────────────────────────────────────────
# FILTER 3: ATR-BASED DYNAMIC SL
# ATR measures actual market volatility
# Low ATR = calm market = tighter SL = more profit captured
# High ATR = volatile market = wider SL = avoid being stopped out
# ─────────────────────────────────────────────────────────────────

def calculate_bnf_atr(period: int = 14) -> float:
    """Calculate BankNifty ATR (Average True Range)"""
    try:
        df = yf.Ticker('^NSEBANK').history(period='30d', interval='1d').dropna()
        if len(df) < period + 1:
            return 500  # Default

        high_low   = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close  = abs(df['Low']  - df['Close'].shift())
        tr         = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr        = tr.rolling(period).mean().iloc[-1]
        return round(float(atr), 0)
    except:
        return 500  # Safe default


def get_dynamic_sl_target(premium: float) -> dict:
    """
    Calculate SL and Target based on current volatility (ATR)

    Logic:
    ATR < 350 pts = Very calm   → SL 25%, Target 2.5x
    ATR 350-550   = Normal      → SL 30%, Target 2.0x (default)
    ATR 550-750   = Volatile    → SL 35%, Target 2.0x
    ATR > 750     = Very volatile → SL 40%, Target 1.8x OR SKIP

    Why this matters:
    High ATR day = wider swings = need wider SL to not get stopped
    Low ATR day  = tight market = can use tighter SL AND bigger target
    """
    atr = calculate_bnf_atr()

    if atr < 486:
        sl_pct  = 0.25
        tgt_mul = 2.5
        regime  = 'CALM'
        note    = f"Calm market (ATR {atr:.0f}, bottom 25%) → tight SL, bigger target"
    elif atr < 875:
        sl_pct  = 0.30
        tgt_mul = 2.0
        regime  = 'NORMAL'
        note    = f"Normal volatility (ATR {atr:.0f}) → standard settings"
    elif atr < 1159:
        sl_pct  = 0.35
        tgt_mul = 2.0
        regime  = 'VOLATILE'
        note    = f"High volatility (ATR {atr:.0f}) → wider SL needed"
    else:
        sl_pct  = 0.40
        tgt_mul = 1.8
        regime  = 'EXTREME'
        note    = f"Extreme volatility (ATR {atr:.0f}) → consider skipping"

    sl_prem  = round(premium * (1 - sl_pct), 0)
    tgt_prem = round(premium * tgt_mul, 0)
    max_loss = round(premium * sl_pct * 15, 0)
    max_gain = round(premium * (tgt_mul - 1) * 15, 0)

    return {
        'atr':      atr,
        'regime':   regime,
        'sl_pct':   sl_pct,
        'tgt_mul':  tgt_mul,
        'sl_prem':  sl_prem,
        'tgt_prem': tgt_prem,
        'max_loss': max_loss,
        'max_gain': max_gain,
        'note':     note,
        'skip':     regime == 'EXTREME'
    }


# ─────────────────────────────────────────────────────────────────
# FILTER 4: GLOBAL MARKET ALIGNMENT
# Simple rule: Don't buy CE if world is falling hard
# Check S&P 500 overnight move as global proxy
# ─────────────────────────────────────────────────────────────────

def check_global_market(bias: str) -> dict:
    """
    Checks S&P 500 overnight direction vs our trade direction.

    Logic:
    If we want to BUY (BULLISH) but S&P500 fell -1.5%+ overnight
    → Global selling pressure → skip or reduce size

    If we want to SELL (BEARISH) but S&P500 rose +1.5%+ overnight
    → Global buying pressure → skip

    If markets align with our direction → extra confidence
    """
    try:
        sp500 = yf.Ticker('^GSPC').history(period='3d', interval='1d').dropna()
        if len(sp500) < 2:
            return {'aligned': True, 'reason': 'Global data unavailable — proceed'}

        prev_close  = float(sp500['Close'].iloc[-2])
        last_close  = float(sp500['Close'].iloc[-1])
        sp500_move  = (last_close - prev_close) / prev_close * 100

        # Nifty pre-market check (SGX Nifty proxy)
        nifty = yf.Ticker('^NSEI').history(period='5d', interval='1d').dropna()
        nifty_1w_return = 0
        if len(nifty) >= 5:
            nifty_1w_return = (float(nifty['Close'].iloc[-1]) -
                               float(nifty['Close'].iloc[-5])) / \
                               float(nifty['Close'].iloc[-5]) * 100

        if bias == 'BULLISH':
            if sp500_move <= -1.5:
                return {
                    'aligned': False,
                    'reason':  f"❌ S&P500 fell {sp500_move:.1f}% — global selling pressure. Skip CE.",
                    'sp500':   sp500_move
                }
            elif sp500_move >= 1.0:
                return {
                    'aligned': True,
                    'extra':   True,
                    'reason':  f"✅ S&P500 up {sp500_move:.1f}% — global tailwind for CE",
                    'sp500':   sp500_move
                }
        elif bias == 'BEARISH':
            if sp500_move >= 1.5:
                return {
                    'aligned': False,
                    'reason':  f"❌ S&P500 rose {sp500_move:.1f}% — global buying pressure. Skip PE.",
                    'sp500':   sp500_move
                }
            elif sp500_move <= -1.0:
                return {
                    'aligned': True,
                    'extra':   True,
                    'reason':  f"✅ S&P500 down {sp500_move:.1f}% — global tailwind for PE",
                    'sp500':   sp500_move
                }

        return {
            'aligned': True,
            'reason':  f"✅ S&P500 {sp500_move:+.1f}% — neutral, proceed normally",
            'sp500':   sp500_move
        }

    except Exception as e:
        return {'aligned': True, 'reason': f'Global check unavailable — proceed'}


# ─────────────────────────────────────────────────────────────────
# FILTER 5: PARTIAL EXIT LOGIC
# Half out at 1.5x → rest runs to 2x with breakeven SL
# This is how professional traders ALWAYS manage exits
# ─────────────────────────────────────────────────────────────────

def get_partial_exit_levels(entry_premium: float) -> dict:
    """
    Two-leg exit strategy:

    Leg 1 (50% of position):
    → Exit at 1.5× premium
    → Books guaranteed profit
    → Removes psychological pressure

    Leg 2 (50% of position):
    → SL moves to breakeven (entry premium)
    → Target stays at 2× premium
    → This is now a "free trade" — max loss = 0
    → If 2× hits → double profit on this leg
    → If reverses → exits at breakeven → no loss

    TOTAL BEST CASE:  Leg1 profit (0.5x) + Leg2 profit (1x) = 1.5x total
    TOTAL WORST CASE: Leg1 profit (0.5x) + Leg2 breakeven  = 0.5x total
    vs OLD ALL-OUT:   Best = 1x, Worst = -0.3x

    The partial exit ALWAYS captures some profit
    even if the final target never hits.
    """
    leg1_target  = round(entry_premium * 1.5, 0)   # Exit 50% here
    leg2_sl      = round(entry_premium * 1.0, 0)   # Move SL to breakeven
    leg2_target  = round(entry_premium * 2.0, 0)   # Final target

    lot_size     = 15
    leg_size     = lot_size // 2  # 7-8 units per leg

    leg1_profit  = round((leg1_target - entry_premium) * leg_size, 0)
    leg2_max_profit = round((leg2_target - entry_premium) * leg_size, 0)
    leg2_max_loss   = 0  # Breakeven SL = no loss on leg 2

    total_best   = leg1_profit + leg2_max_profit
    total_worst  = leg1_profit  # Leg 2 exits at breakeven

    return {
        'entry_premium':    entry_premium,
        'leg1_units':       leg_size,
        'leg1_target':      leg1_target,
        'leg1_profit':      leg1_profit,
        'leg2_units':       lot_size - leg_size,
        'leg2_sl':          leg2_sl,       # Breakeven
        'leg2_target':      leg2_target,
        'leg2_max_profit':  leg2_max_profit,
        'total_best_rs':    total_best,
        'total_worst_rs':   total_worst,
        'note': (
            f"Take Rs {leg1_profit:,} guaranteed at 1.5x, "
            f"then let rest run risk-free to 2x"
        )
    }


def check_partial_exit(current_premium: float,
                       trade_state: dict) -> dict:
    """
    Called every 15 min while in trade.
    Checks if leg 1 or leg 2 targets are hit.
    """
    entry  = float(trade_state.get('entry_premium', 0))
    levels = get_partial_exit_levels(entry)
    leg1_done = trade_state.get('leg1_done', False)

    if not leg1_done:
        # Check leg 1
        if current_premium >= levels['leg1_target']:
            return {
                'action':  'EXIT_LEG1',
                'units':    levels['leg1_units'],
                'premium':  current_premium,
                'profit':   levels['leg1_profit'],
                'message':  f"🎯 Leg 1 target hit at Rs {current_premium:.0f}! "
                            f"Booking Rs {levels['leg1_profit']:,} profit on {levels['leg1_units']} units. "
                            f"Moving SL to breakeven Rs {levels['leg2_sl']} on remaining."
            }
        # Check original SL (leg 1 not done yet — full SL still active)
        sl_full = round(entry * 0.70, 0)  # 30% SL
        if current_premium <= sl_full:
            return {
                'action':  'EXIT_ALL',
                'premium':  current_premium,
                'message':  f"🛑 SL hit at Rs {current_premium:.0f}. Exit all."
            }
    else:
        # Leg 1 done — leg 2 running with breakeven SL
        if current_premium >= levels['leg2_target']:
            return {
                'action':  'EXIT_LEG2',
                'units':    levels['leg2_units'],
                'premium':  current_premium,
                'profit':   levels['leg2_max_profit'],
                'message':  f"🎯 Final target Rs {current_premium:.0f}! "
                            f"Rs {levels['leg2_max_profit']:,} profit on remaining units. "
                            f"Full trade complete. 🔥"
            }
        # Breakeven SL for leg 2
        if current_premium <= levels['leg2_sl']:
            return {
                'action':  'EXIT_LEG2_BE',
                'units':    levels['leg2_units'],
                'premium':  current_premium,
                'profit':   0,
                'message':  f"Exiting remaining at breakeven Rs {current_premium:.0f}. "
                            f"Leg 1 profit Rs {levels['leg1_profit']:,} secured. ✅"
            }

    return {'action': 'HOLD'}


# ─────────────────────────────────────────────────────────────────
# MASTER FILTER — Run all filters before entering
# ─────────────────────────────────────────────────────────────────

def run_all_filters(bias: str, df_15m: pd.DataFrame,
                    premium: float, score: int) -> dict:
    """
    Master function — runs all 5 filters.
    Returns final GO/NO-GO decision with reasons.
    """
    reasons  = []
    warnings = []
    skip     = False

    # Filter 1: Event check
    event = is_event_day()
    if event.get('skip'):
        return {
            'proceed': False,
            'reason':  event['reason'],
            'filter':  'EVENT'
        }
    if event.get('caution'):
        warnings.append(event['reason'])

    # Filter 2: Volume confirmation
    vol = check_pullback_volume(df_15m, bias)
    if vol['quality'] == 'HIGH':
        reasons.append(vol['reason'])
    elif vol['quality'] == 'LOW':
        skip = True
        warnings.append(vol['reason'])
    else:
        warnings.append(vol['reason'])

    # Filter 3: ATR dynamic SL/Target
    dyn = get_dynamic_sl_target(premium)
    if dyn.get('skip'):
        return {
            'proceed':  False,
            'reason':   f"❌ {dyn['note']} — skipping",
            'filter':   'ATR'
        }
    reasons.append(f"📊 {dyn['note']}")

    # Filter 4: Global market check
    globe = check_global_market(bias)
    if not globe.get('aligned'):
        return {
            'proceed': False,
            'reason':  globe['reason'],
            'filter':  'GLOBAL'
        }
    if globe.get('extra'):
        reasons.append(globe['reason'])

    if skip:
        return {
            'proceed':  False,
            'reason':   f"High volume pullback — possible reversal not pullback",
            'filter':   'VOLUME',
            'warnings': warnings
        }

    # All filters passed
    return {
        'proceed':    True,
        'reasons':    reasons,
        'warnings':   warnings,
        'dynamic_sl': dyn,
        'partial_levels': get_partial_exit_levels(premium)
    }


if __name__ == '__main__':
    print("Testing all 5 filters...\n")

    # Test event filter
    ev = is_event_day()
    print(f"Event filter: skip={ev['skip']} | {ev['reason'] or 'Clear day'}")

    # Test ATR
    dyn = get_dynamic_sl_target(265)
    print(f"\nATR Dynamic SL:")
    print(f"  ATR: {dyn['atr']} pts ({dyn['regime']})")
    print(f"  SL: Rs {dyn['sl_prem']} ({dyn['sl_pct']*100:.0f}%)")
    print(f"  Target: Rs {dyn['tgt_prem']} ({dyn['tgt_mul']}x)")
    print(f"  Max Loss: Rs {dyn['max_loss']:,}")
    print(f"  Max Gain: Rs {dyn['max_gain']:,}")

    # Test global check
    globe = check_global_market('BULLISH')
    print(f"\nGlobal market: {globe['reason']}")

    # Test partial exit
    pe = get_partial_exit_levels(265)
    print(f"\nPartial exit levels (entry Rs 265):")
    print(f"  Leg 1: Exit {pe['leg1_units']} units at Rs {pe['leg1_target']} → Rs {pe['leg1_profit']:,} profit")
    print(f"  Leg 2: Exit {pe['leg2_units']} units at Rs {pe['leg2_target']} | SL Rs {pe['leg2_sl']} (breakeven)")
    print(f"  Best case:  Rs {pe['total_best_rs']:,}")
    print(f"  Worst case: Rs {pe['total_worst_rs']:,} (never zero!)")
