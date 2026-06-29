"""
Intraday BankNifty Scanner
Runs every 15 minutes during market hours
Finds live entry and exit signals on 15-min chart
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, time as dtime
import pytz
import json
import os
import warnings
warnings.filterwarnings('ignore')

IST      = pytz.timezone('Asia/Kolkata')
SYMBOL   = '^NSEBANK'
STATE_FILE = 'trade_state.json'

# ── Market Hours ─────────────────────────────────────────────────
MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 20)  # exit before 3:30


def is_market_open() -> bool:
    now = datetime.now(IST).time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def get_15min_data() -> pd.DataFrame:
    try:
        df = yf.Ticker(SYMBOL).history(period='5d', interval='15m')
        return df.dropna() if len(df) > 20 else None
    except:
        return None


def get_daily_bias() -> str:
    """Quick daily trend check"""
    try:
        df = yf.Ticker(SYMBOL).history(period='3mo', interval='1d').dropna()
        if len(df) < 10: return 'NEUTRAL'
        highs, lows = [], []
        for i in range(3, len(df)-3):
            if all(df['High'].iloc[i] >= df['High'].iloc[i-j] for j in range(1,4)) and \
               all(df['High'].iloc[i] >= df['High'].iloc[i+j] for j in range(1,4)):
                highs.append(df['High'].iloc[i])
            if all(df['Low'].iloc[i] <= df['Low'].iloc[i-j] for j in range(1,4)) and \
               all(df['Low'].iloc[i] <= df['Low'].iloc[i+j] for j in range(1,4)):
                lows.append(df['Low'].iloc[i])
        if len(highs)>=2 and len(lows)>=2:
            if highs[-1]>highs[-2] and lows[-1]>lows[-2]: return 'BULLISH'
            if highs[-1]<highs[-2] and lows[-1]<lows[-2]: return 'BEARISH'
        return 'NEUTRAL'
    except:
        return 'NEUTRAL'


def find_intraday_ob(df: pd.DataFrame, trend: str) -> list:
    """Find Order Blocks on 15-min chart"""
    obs = []
    recent = df.tail(50)
    for i in range(3, len(recent)-1):
        up = (recent['High'].iloc[i+1:i+3].max() - recent['High'].iloc[i]) / recent['High'].iloc[i]
        dn = (recent['Low'].iloc[i] - recent['Low'].iloc[i+1:i+3].min()) / recent['Low'].iloc[i]
        if trend in ['BULLISH'] and recent['Close'].iloc[i] < recent['Open'].iloc[i] and up >= 0.003:
            obs.append({
                'type': 'BUY',
                'high': round(recent['High'].iloc[i], 2),
                'low':  round(recent['Low'].iloc[i], 2),
                'mid':  round((recent['High'].iloc[i]+recent['Low'].iloc[i])/2, 2)
            })
        if trend in ['BEARISH'] and recent['Close'].iloc[i] > recent['Open'].iloc[i] and dn >= 0.003:
            obs.append({
                'type': 'SELL',
                'high': round(recent['High'].iloc[i], 2),
                'low':  round(recent['Low'].iloc[i], 2),
                'mid':  round((recent['High'].iloc[i]+recent['Low'].iloc[i])/2, 2)
            })
    return obs[-5:]


def find_intraday_fvg(df: pd.DataFrame, trend: str) -> list:
    """FVGs on 15-min chart"""
    fvgs = []
    recent = df.tail(30)
    for i in range(2, len(recent)):
        if trend == 'BULLISH' and recent['Low'].iloc[i] > recent['High'].iloc[i-2]:
            fvgs.append({
                'type':   'BUY',
                'bottom': round(recent['High'].iloc[i-2], 2),
                'top':    round(recent['Low'].iloc[i], 2),
                'mid':    round((recent['High'].iloc[i-2]+recent['Low'].iloc[i])/2, 2)
            })
        if trend == 'BEARISH' and recent['Low'].iloc[i-2] > recent['High'].iloc[i]:
            fvgs.append({
                'type':   'SELL',
                'top':    round(recent['Low'].iloc[i-2], 2),
                'bottom': round(recent['High'].iloc[i], 2),
                'mid':    round((recent['Low'].iloc[i-2]+recent['High'].iloc[i])/2, 2)
            })
    return fvgs[-5:]


def detect_choch(df: pd.DataFrame, trend: str) -> bool:
    """CHoCH on last 10 candles"""
    recent = df.tail(10)
    for i in range(2, len(recent)):
        if trend == 'BULLISH':
            if recent['Close'].iloc[i] > recent['High'].iloc[i-2] and \
               recent['Close'].iloc[i-1] < recent['Open'].iloc[i-1]:
                return True
        if trend == 'BEARISH':
            if recent['Close'].iloc[i] < recent['Low'].iloc[i-2] and \
               recent['Close'].iloc[i-1] > recent['Open'].iloc[i-1]:
                return True
    return False


def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)


def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except:
        pass
    return {'in_trade': False, 'trade': None}


def next_expiry():
    from src.expiry_picker import next_banknifty_expiry
    return next_banknifty_expiry()


def check_exit_signal(df: pd.DataFrame, trade: dict) -> dict:
    """
    Check if open trade should be exited
    Returns: {'exit': True/False, 'reason': '', 'urgency': 'normal/urgent'}
    """
    if not trade:
        return {'exit': False}

    current    = float(df['Close'].iloc[-1])
    entry_prem = float(trade.get('entry_premium', 0))
    opt_type   = trade.get('opt_type', 'CE')

    # Estimate current premium (simplified)
    # Real implementation would use Groww option chain API
    bnf_entry  = float(trade.get('bnf_entry', current))
    bnf_move   = current - bnf_entry

    if opt_type == 'CE':
        est_premium = entry_prem + (bnf_move * 0.5)  # rough delta 0.5
    else:
        est_premium = entry_prem - (bnf_move * 0.5)

    tgt_prem = float(trade.get('tgt_prem', entry_prem * 2))
    sl_prem  = float(trade.get('sl_prem',  entry_prem * 0.7))
    pnl_pct  = ((est_premium - entry_prem) / entry_prem * 100) if entry_prem else 0

    # Target hit
    if est_premium >= tgt_prem:
        return {
            'exit':    True,
            'reason':  f'🎯 TARGET HIT! Premium ~Rs {est_premium:.0f} (target Rs {tgt_prem})',
            'urgency': 'urgent',
            'pnl_pct': pnl_pct
        }

    # SL hit
    if est_premium <= sl_prem:
        return {
            'exit':    True,
            'reason':  f'🛑 STOP LOSS. Premium ~Rs {est_premium:.0f} (SL Rs {sl_prem})',
            'urgency': 'urgent',
            'pnl_pct': pnl_pct
        }

    # End of day exit (3:10 PM)
    now = datetime.now(IST).time()
    if now >= dtime(15, 10):
        return {
            'exit':    True,
            'reason':  f'⏰ EOD exit. Premium ~Rs {est_premium:.0f}',
            'urgency': 'urgent',
            'pnl_pct': pnl_pct
        }

    # Trailing: up 60% → protect gains
    if pnl_pct >= 60:
        trail_sl = entry_prem * 1.35  # 35% profit floor
        if est_premium <= trail_sl:
            return {
                'exit':    True,
                'reason':  f'📈 Trailing SL hit. Protecting +{pnl_pct:.0f}% gain',
                'urgency': 'normal',
                'pnl_pct': pnl_pct
            }

    return {
        'exit':    False,
        'pnl_pct': pnl_pct,
        'est_prem': round(est_premium, 0)
    }


def scan_intraday() -> dict:
    """
    Main intraday scan — called every 15 minutes
    Returns action dict: entry/exit/hold/no_trade
    """
    now       = datetime.now(IST)
    timestamp = now.strftime('%d %b %Y %I:%M %p IST')

    if not is_market_open():
        return {
            'action':    'CLOSED',
            'message':   f'Market closed | {timestamp}',
            'timestamp': timestamp
        }

    df_15   = get_15min_data()
    if df_15 is None:
        return {'action': 'ERROR', 'message': 'Data unavailable'}

    current = float(df_15['Close'].iloc[-1])
    state   = load_state()

    # ── Already in trade → check exit ────────────────────────────
    if state.get('in_trade') and state.get('trade'):
        exit_check = check_exit_signal(df_15, state['trade'])
        trade      = state['trade']
        lot_cost   = float(trade.get('entry_premium', 0)) * 15

        if exit_check.get('exit'):
            pnl_pct = exit_check.get('pnl_pct', 0)
            pnl_rs  = round(lot_cost * pnl_pct / 100, 0)
            state['in_trade'] = False
            state['trade']    = None
            save_state(state)
            return {
                'action':    'EXIT',
                'urgent':    exit_check.get('urgency') == 'urgent',
                'reason':    exit_check['reason'],
                'pnl_pct':   round(pnl_pct, 1),
                'pnl_rs':    pnl_rs,
                'option':    trade.get('name'),
                'timestamp': timestamp,
                'current':   round(current, 2)
            }
        else:
            est   = exit_check.get('est_prem', 0)
            pnl_r = round(float(trade.get('entry_premium',0)) * exit_check.get('pnl_pct',0)/100 * 15, 0)
            return {
                'action':    'HOLD',
                'option':    trade.get('name'),
                'pnl_pct':   round(exit_check.get('pnl_pct', 0), 1),
                'pnl_rs':    pnl_r,
                'est_prem':  est,
                'tgt_prem':  trade.get('tgt_prem'),
                'sl_prem':   trade.get('sl_prem'),
                'timestamp': timestamp,
                'current':   round(current, 2)
            }

    # ── No trade → look for entry ─────────────────────────────────
    # Don't enter after 2:00 PM (not enough time)
    if now.time() >= dtime(14, 0):
        return {
            'action':    'NO_ENTRY',
            'message':   'After 2 PM — too late for new entry today',
            'timestamp': timestamp,
            'current':   round(current, 2)
        }

    daily_bias = get_daily_bias()
    if daily_bias == 'NEUTRAL':
        return {
            'action':    'WAIT',
            'message':   'Daily chart neutral — no intraday trade',
            'timestamp': timestamp,
            'current':   round(current, 2)
        }

    obs    = find_intraday_ob(df_15, daily_bias)
    fvgs   = find_intraday_fvg(df_15, daily_bias)
    choch  = detect_choch(df_15, daily_bias)

    score   = 0
    reasons = []
    entry_zone = None

    ob_type = 'BUY' if daily_bias == 'BULLISH' else 'SELL'

    # Check OB
    for ob in reversed(obs):
        if ob['type'] == ob_type and ob['low'] <= current <= ob['high'] * 1.005:
            score += 3
            reasons.append(f"✅ 15M OB zone: {ob['low']:,.0f}–{ob['high']:,.0f}")
            entry_zone = ob['mid']
            break

    # Check FVG
    for fvg in reversed(fvgs):
        if fvg['type'] == ob_type:
            if fvg['bottom'] <= current <= fvg['top'] * 1.005:
                score += 2
                reasons.append(f"✅ 15M FVG: {fvg['bottom']:,.0f}–{fvg['top']:,.0f}")
                if not entry_zone: entry_zone = fvg['mid']
                break

    # CHoCH confirmation
    if choch:
        score += 2
        reasons.append(f"✅ 15M CHoCH confirmed")

    # Daily bias
    score += 2
    reasons.append(f"✅ Daily {daily_bias}")

    # Minimum score and entry zone required
    if score < 5 or not entry_zone:
        return {
            'action':    'WAIT',
            'message':   f'Score {score}/9 — waiting for better setup',
            'reasons':   reasons,
            'timestamp': timestamp,
            'current':   round(current, 2),
            'daily_bias': daily_bias
        }

    # Build trade
    from src.scanner import strike_and_premium, next_expiry
    strike_data = strike_and_premium(current, daily_bias)
    expiry      = next_expiry()

    # Save state
    trade_state = {
        'in_trade': True,
        'trade': {
            'name':          strike_data['name'],
            'strike':        strike_data['strike'],
            'opt_type':      strike_data['opt_type'],
            'expiry':        expiry,
            'entry_premium': strike_data['premium'],
            'sl_prem':       strike_data['sl_prem'],
            'tgt_prem':      strike_data['tgt_prem'],
            'bnf_entry':     current,
            'entry_time':    timestamp
        }
    }
    save_state(trade_state)

    return {
        'action':    'ENTER',
        'score':     score,
        'daily_bias': daily_bias,
        'reasons':   reasons,
        'current':   round(current, 2),
        'name':      strike_data['name'],
        'strike':    strike_data['strike'],
        'opt_type':  strike_data['opt_type'],
        'expiry':    expiry,
        'premium':   strike_data['premium'],
        'sl_prem':   strike_data['sl_prem'],
        'tgt_prem':  strike_data['tgt_prem'],
        'lot_cost':  strike_data['lot_cost'],
        'max_loss':  strike_data['max_loss'],
        'max_profit':strike_data['max_profit'],
        'timestamp': timestamp
    }


if __name__ == '__main__':
    r = scan_intraday()
    print(f"Action: {r['action']}")
    for k, v in r.items():
        if k != 'action':
            print(f"  {k}: {v}")
