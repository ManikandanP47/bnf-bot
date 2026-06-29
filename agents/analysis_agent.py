"""
Analysis Agent — 3-Timeframe Entry Decision Matrix
10-year trader approach: Top-down analysis

TIMEFRAME PURPOSE:
  15-min → Market structure (trend direction)
  5-min  → Setup formation (OB/FVG/CHoCH)
  1-min  → Entry timing (precise trigger)

ENTRY RULE (ALL must be true):
  1. Daily zone: price at saved evening OB
  2. 15-min:     BULLISH structure (HH + HL)
  3. 5-min:      CHoCH confirmed
  4. 1-min:      Bullish close above key level
  5. VWAP:       Price above VWAP (for CE)
  6. RSI:        5-min RSI between 40-65 (not overbought)
  7. Session:    MORNING_TREND or AFTERNOON_MOVE only
"""

import threading, time
from datetime import datetime, time as dtime
import pytz

from core.shared_state import STATE
IST = pytz.timezone('Asia/Kolkata')


def get_structure(candles: list) -> dict:
    """
    Detect market structure from candles.
    Finds swing highs and lows.
    Returns: BULLISH / BEARISH / NEUTRAL
    """
    if len(candles) < 10:
        return {'trend': 'NEUTRAL', 'reason': 'Not enough data'}

    highs, lows = [], []
    for i in range(3, len(candles)-3):
        if all(candles[i]['high'] >= candles[i-j]['high'] for j in range(1,4)) and \
           all(candles[i]['high'] >= candles[i+j]['high'] for j in range(1,4)):
            highs.append(candles[i]['high'])
        if all(candles[i]['low'] <= candles[i-j]['low'] for j in range(1,4)) and \
           all(candles[i]['low'] <= candles[i+j]['low'] for j in range(1,4)):
            lows.append(candles[i]['low'])

    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]   # Higher High
        hl = lows[-1]  > lows[-2]    # Higher Low
        lh = highs[-1] < highs[-2]   # Lower High
        ll = lows[-1]  < lows[-2]    # Lower Low

        if hh and hl:
            return {
                'trend':      'BULLISH',
                'last_high':  highs[-1],
                'last_low':   lows[-1],
                'reason':     f"HH {highs[-1]:,.0f} + HL {lows[-1]:,.0f}"
            }
        if lh and ll:
            return {
                'trend':      'BEARISH',
                'last_high':  highs[-1],
                'last_low':   lows[-1],
                'reason':     f"LH {highs[-1]:,.0f} + LL {lows[-1]:,.0f}"
            }

    return {'trend': 'NEUTRAL', 'reason': 'Mixed signals'}


def check_choch(candles: list, trend: str) -> dict:
    """
    Change of Character — first sign reversal/continuation.
    Bullish CHoCH: price was pulling back, now one candle
    closes ABOVE a previous swing high → buyers in control.
    """
    if len(candles) < 5:
        return {'confirmed': False}

    recent = candles[-8:]
    for i in range(2, len(recent)):
        if trend == 'BULLISH':
            # Price closed above a recent high (after pullback)
            if (recent[i]['close'] > recent[i-2]['high'] and
                    recent[i-1]['close'] < recent[i-1]['open']):
                return {
                    'confirmed': True,
                    'level':     recent[i-2]['high'],
                    'reason':    f"5M CHoCH above {recent[i-2]['high']:,.0f}"
                }
        if trend == 'BEARISH':
            if (recent[i]['close'] < recent[i-2]['low'] and
                    recent[i-1]['close'] > recent[i-1]['open']):
                return {
                    'confirmed': True,
                    'level':     recent[i-2]['low'],
                    'reason':    f"5M CHoCH below {recent[i-2]['low']:,.0f}"
                }
    return {'confirmed': False}


def check_1min_trigger(candles_1m: list, trend: str,
                        vwap: float) -> dict:
    """
    1-min entry trigger — final confirmation.
    Bullish: strong 1-min candle closing above VWAP
    with RSI turning up.
    """
    if len(candles_1m) < 3:
        return {'ready': False}

    last    = candles_1m[-1]
    prev    = candles_1m[-2]

    if trend == 'BULLISH':
        # Strong bullish 1-min close
        body    = last['close'] - last['open']
        candle_range = last['high'] - last['low']
        body_pct = body / candle_range if candle_range > 0 else 0

        above_vwap  = last['close'] > vwap if vwap > 0 else True
        bullish_body = body > 0  # Green candle
        strong_close = body_pct > 0.5  # Body is >50% of range

        if bullish_body and above_vwap and strong_close:
            return {
                'ready':  True,
                'reason': f"1M bullish close {last['close']:,.0f} above VWAP {vwap:,.0f}"
            }

    if trend == 'BEARISH':
        body      = last['open'] - last['close']
        candle_range = last['high'] - last['low']
        body_pct  = body / candle_range if candle_range > 0 else 0
        below_vwap = last['close'] < vwap if vwap > 0 else True
        bearish_body = last['close'] < last['open']
        strong_close = body_pct > 0.5

        if bearish_body and below_vwap and strong_close:
            return {
                'ready':  True,
                'reason': f"1M bearish close {last['close']:,.0f} below VWAP {vwap:,.0f}"
            }

    return {'ready': False, 'reason': 'Waiting for 1M trigger'}


def check_volume_quality(candles_5m: list) -> dict:
    """
    Pullback should have LOWER volume than the initial move.
    Low volume pullback = real pause (not reversal).
    High volume pullback = possible reversal (skip entry).
    """
    if len(candles_5m) < 10:
        return {'quality': 'UNKNOWN', 'ratio': 1.0}

    avg_vol     = sum(c['volume'] for c in candles_5m[-20:-3]) / 17
    recent_vol  = sum(c['volume'] for c in candles_5m[-3:]) / 3
    ratio       = round(recent_vol / avg_vol, 2) if avg_vol > 0 else 1.0

    if ratio < 0.70:
        return {
            'quality': 'GOOD',
            'ratio':   ratio,
            'reason':  f"✅ Low volume pullback ({ratio:.1%}) — genuine pause"
        }
    elif ratio < 1.0:
        return {
            'quality': 'OK',
            'ratio':   ratio,
            'reason':  f"⚠️ Normal volume pullback ({ratio:.1%})"
        }
    else:
        return {
            'quality': 'BAD',
            'ratio':   ratio,
            'reason':  f"❌ High volume pullback ({ratio:.1%}) — skip"
        }


class AnalysisAgent(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name='AnalysisAgent')
        self.last_signal_time = None

    def analyse(self):
        """
        Full 3-timeframe analysis.
        Every condition must pass. No shortcuts.
        """
        price      = STATE.get('market.price', 0)
        c1m        = STATE.get('market.candles_1m', [])
        c5m        = STATE.get('market.candles_5m', [])
        c15m       = STATE.get('market.candles_15m', [])
        session    = STATE.get('market.session', 'CLOSED')
        rsi_5m     = STATE.get('market.rsi_5m', 50)
        rsi_1m     = STATE.get('market.rsi_1m', 50)
        vwap       = STATE.get('market.vwap', 0)
        zone       = STATE.get('zone', {})

        if price == 0 or len(c5m) < 10:
            return

        # ── PAUSE CHECK ───────────────────────────────────────────
        # Respect /pause command from Telegram
        if STATE.get('system.paused', False):
            return

        # ── SESSION FILTER ────────────────────────────────────────
        # Only trade MORNING_TREND or AFTERNOON_MOVE
        # 10-year rule: never trade opening chaos or lunch
        tradeable_sessions = ['MORNING_TREND', 'AFTERNOON_MOVE']
        if session not in tradeable_sessions:
            return  # Silent — no spam

        # ── RSI FILTER ────────────────────────────────────────────
        # 5-min RSI: 40-65 = healthy zone for CE entry
        # Above 70 = overbought (too late to buy)
        # Below 30 = oversold (if BULLISH)
        if rsi_5m > 70 or rsi_5m < 30:
            return  # Extreme RSI — not the right moment

        # ── EVENING ZONE CHECK ────────────────────────────────────
        zone_active = zone.get('active', False)
        zone_used   = zone.get('used', False)
        zone_low    = zone.get('low', 0)
        zone_high   = zone.get('high', 0)

        if not zone_active or zone_used:
            return  # No zone or already used today

        # Is price in the zone?
        in_zone = zone_low * 0.994 <= price <= zone_high * 1.006
        if not in_zone:
            return  # Price not at our level yet

        # ── TIMEFRAME 1: 15-MIN STRUCTURE ────────────────────────
        # Top-down: 15-min must confirm overall direction
        struct_15m = get_structure(c15m)
        bias        = zone.get('bias', 'NEUTRAL')

        if struct_15m['trend'] != bias:
            # 15-min disagrees with daily bias — risky trade
            if struct_15m['trend'] == 'NEUTRAL':
                pass  # Neutral is OK — not against us
            else:
                return  # 15-min AGAINST daily bias — skip

        # ── TIMEFRAME 2: 5-MIN CHOCH ─────────────────────────────
        # 5-min CHoCH = sellers exhausted, buyers stepping in
        choch_5m = check_choch(c5m, bias)
        if not choch_5m.get('confirmed'):
            return  # No confirmation yet — wait

        # ── VOLUME QUALITY ────────────────────────────────────────
        vol_check = check_volume_quality(c5m)
        if vol_check['quality'] == 'BAD':
            return  # High volume pullback = not genuine

        # ── TIMEFRAME 3: 1-MIN TRIGGER ───────────────────────────
        # Final entry timing — 1-min must also confirm
        trigger_1m = check_1min_trigger(c1m, bias, vwap)
        if not trigger_1m.get('ready'):
            return  # Wait for 1-min confirmation

        # ── ALL CONDITIONS MET — BUILD SIGNAL ────────────────────
        score   = 5  # Base score
        reasons = []

        # Score components
        reasons.append(f"✅ Price at {bias} zone: {zone_low:,.0f}–{zone_high:,.0f}")
        score += 1

        if struct_15m['trend'] == bias:
            reasons.append(f"✅ 15M structure {bias}: {struct_15m['reason']}")
            score += 2
        else:
            reasons.append(f"⚠️ 15M neutral — daily {bias} bias")

        reasons.append(f"✅ 5M CHoCH: {choch_5m['reason']}")
        score += 2

        if vol_check['quality'] == 'GOOD':
            reasons.append(f"✅ {vol_check['reason']}")
            score += 1

        reasons.append(f"✅ {trigger_1m['reason']}")
        score += 1

        reasons.append(f"✅ RSI 5M: {rsi_5m} (healthy zone)")
        if 45 <= rsi_5m <= 60:
            score += 1

        if vwap > 0:
            vwap_ok = (price > vwap if bias == 'BULLISH' else price < vwap)
            if vwap_ok:
                reasons.append(f"✅ VWAP aligned: price {'above' if bias=='BULLISH' else 'below'} {vwap:,.0f}")
                score += 1

        # Prevent signal spam — max 1 signal per 30 min
        now = datetime.now(IST)
        if self.last_signal_time:
            mins_since = (now - self.last_signal_time).total_seconds() / 60
            if mins_since < 30:
                return

        if STATE.get('position.open'):
            return
        if STATE.get('signals.awaiting_confirmation') or STATE.get('signals.confirmation_sent'):
            return

        # Trading knowledge — levels, theta, candles, history alignment
        signal_preview = {
            'price': price, 'trend': bias, 'session': session, 'score': score,
        }
        from src.trading_knowledge import run_knowledge_checks
        know = run_knowledge_checks(signal_preview, c5m)
        if not know.get('ok', True):
            from src.trade_analytics import log_funnel
            log_funnel('knowledge_block', signal_preview, know.get('reason', ''))
            return
        score += know.get('score_delta', 0)
        for w in know.get('warnings', []):
            reasons.append(w)
        if know.get('patterns'):
            reasons.append(f"✅ 5M: {', '.join(know['patterns'])}")

        # Publish signal
        STATE.update('signals', {
            'analysis_ready': True,
            'analysis': {
                'ready':       True,
                'score':       score,
                'trend':       bias,
                'price':       price,
                'session':     session,
                'regime':      STATE.get('market.regime', 'TRENDING'),
                'rsi':         rsi_5m,
                'rsi_5m':      rsi_5m,
                'rsi_1m':      rsi_1m,
                'vwap':        vwap,
                'choch':       choch_5m,
                'struct_15m':  struct_15m,
                'vol_quality': vol_check['quality'],
                'reasons':     reasons,
                'time':        now.strftime('%H:%M:%S')
            }
        })

        from src.trade_analytics import log_funnel
        log_funnel('setup_seen', {
            'score': score, 'trend': bias, 'session': session,
        })

        self.last_signal_time = now
        print(f"📊 Signal: {bias} score={score} at {price:,.0f} | {now.strftime('%H:%M')}")

    def run(self):
        STATE.set_agent_status('analysis', 'RUNNING')
        print("📊 Analysis Agent: 15M + 5M + 1M timeframes ✅")

        while STATE.get('system.running'):
            try:
                if STATE.get('system.market_open'):
                    self.analyse()
            except Exception as e:
                STATE.add_error(f"Analysis: {str(e)[:60]}")
            time.sleep(15)  # Check every 15 seconds

        STATE.set_agent_status('analysis', 'STOPPED')
