"""
Data Agent — Real-Time Market Feed
Builds 3 timeframes: 1-min, 5-min, 15-min
10-year trader rule: ALWAYS use multiple timeframes
"""

import threading, time, json, os, warnings, requests
from datetime import datetime, time as dtime
from collections import deque
import pytz
warnings.filterwarnings('ignore')

from core.shared_state import STATE
IST = pytz.timezone('Asia/Kolkata')

BANKNIFTY_SYMBOL = "NSE_BANKNIFTY"


class CandleBuilder:
    def __init__(self, interval_min: int):
        self.interval = interval_min * 60
        self.candles  = deque(maxlen=150)
        self.current  = None
        self._lock    = threading.Lock()
        self.interval_min = interval_min

    def add_tick(self, price: float, volume: int, ts: datetime):
        with self._lock:
            bucket = int(ts.timestamp() / self.interval) * self.interval
            if self.current is None or self.current['bucket'] != bucket:
                if self.current:
                    self.candles.append(dict(self.current))
                self.current = {
                    'bucket': bucket,
                    'open':   price,
                    'high':   price,
                    'low':    price,
                    'close':  price,
                    'volume': volume,
                    'time':   ts.strftime('%H:%M'),
                    'new':    True   # Flag for analysis agent
                }
            else:
                c = self.current
                c['high']   = max(c['high'], price)
                c['low']    = min(c['low'],  price)
                c['close']  = price
                c['volume'] += volume
                c['new']    = False

    def get_candles(self) -> list:
        with self._lock:
            result = list(self.candles)
            if self.current:
                result.append(dict(self.current))
            return result

    def latest_closed(self) -> dict:
        """Get the most recently CLOSED candle (not current forming)"""
        with self._lock:
            if self.candles:
                return dict(self.candles[-1])
        return {}


class DataAgent(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name='DataAgent')
        # THREE timeframes — critical for proper trading
        self.b1  = CandleBuilder(1)   # 1-min:  entry timing
        self.b5  = CandleBuilder(5)   # 5-min:  setup formation
        self.b15 = CandleBuilder(15)  # 15-min: market structure
        self.running = True
        self._token  = None
        self._ctx_tick = 0

    def is_market_time(self) -> bool:
        now = datetime.now(IST).time()
        return dtime(9, 0) <= now <= dtime(15, 50)

    def get_groww_token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            if self._token and self._token.startswith('eyJ'):
                return self._token
            state_tok = STATE.get('system.groww_token', '')
            if state_tok and state_tok.startswith('eyJ'):
                self._token = state_tok
                return state_tok
        try:
            from src.groww_auth import fetch_groww_token
            access_token = fetch_groww_token(
                force_refresh=force_refresh,
                max_retries=1,
                base_delay_sec=120,
            )
            STATE.set('system.groww_token', access_token)
            from src.groww_client import clear_groww_client
            clear_groww_client()
            self._token = access_token
            return access_token
        except Exception as e:
            STATE.add_error(f"Token: {str(e)[:40]}")
            if self._token and self._token.startswith('eyJ'):
                return self._token
        return os.getenv('GROWW_ACCESS_TOKEN', '')

    def refresh_token_if_needed(self):
        """Auto-refresh token once per scheduled slot (8:45, 12:45, 15:45 IST)."""
        now = datetime.now(IST)
        slots = [(8, 45), (12, 45), (15, 45)]
        today = now.strftime('%Y-%m-%d')

        for hour, minute in slots:
            if now.hour != hour or not (minute - 1 <= now.minute <= minute + 1):
                continue
            key = f'system.token_refreshed_{hour:02d}{minute:02d}'
            if STATE.get(key) == today:
                return
            self._token = self.get_groww_token(force_refresh=True)
            STATE.set(key, today)
            STATE.set('system.token_status', f'REFRESHED_{hour}:{minute:02d}')
            print(f"🔑 Token refreshed at {hour}:{minute:02d}")
            return

    def get_live_price(self) -> dict:
        """Get live BankNifty price from Groww with auto-retry on token expiry"""
        try:
            if not self._token:
                self._token = self.get_groww_token()
            from src.groww_client import get_groww_client
            groww = get_groww_client(self._token)
            q = groww.get_ltp(
                exchange_trading_symbols=(BANKNIFTY_SYMBOL,),
                segment=groww.SEGMENT_CASH
            )
            if q and isinstance(q, dict):
                # Handle both response formats:
                # Old: {'ltps': [{'ltp': 58214.3, ...}]}
                # New: {'NSE_BANKNIFTY': 58214.3}
                prices = q.get('ltps', [])
                if prices:
                    p = float(prices[0].get('ltp', 0) or prices[0].get('last_price', 0))
                elif BANKNIFTY_SYMBOL in q:
                    p = float(q[BANKNIFTY_SYMBOL])
                else:
                    p = 0
                    
                if p > 0:
                    return {'price': p, 'volume': 1000, 'source': 'GROWW'}
        except Exception as e:
            # Auth errors only — not symbol/format errors ("invalid symbol" ≠ bad token)
            error_str = str(e).lower()
            auth_err = (
                'unauthorized', 'forbidden', 'expired', 'invalid token',
                'token invalid', 'authentication', '401', '403',
            )
            if any(m in error_str for m in auth_err):
                print(f"🔄 Token expired detected: {str(e)[:40]}")
                from src.groww_client import clear_groww_client
                clear_groww_client()
                self._token = self.get_groww_token()  # Auto-refresh
                # Retry once with new token
                try:
                    from src.groww_client import get_groww_client
                    groww = get_groww_client(self._token)
                    q = groww.get_ltp(
                        exchange_trading_symbols=(BANKNIFTY_SYMBOL,),
                        segment=groww.SEGMENT_CASH
                    )
                    if q and isinstance(q, dict):
                        prices = q.get('ltps', [])
                        if prices:
                            p = float(prices[0].get('ltp', 0) or prices[0].get('last_price', 0))
                        elif BANKNIFTY_SYMBOL in q:
                            p = float(q[BANKNIFTY_SYMBOL])
                        else:
                            p = 0
                            
                        if p > 0:
                            print(f"✅ Recovered with fresh token")
                            return {'price': p, 'volume': 1000, 'source': 'GROWW'}
                except:
                    pass
            else:
                STATE.add_error(f"Price: {str(e)[:40]}")
        return {}

    def get_price_fallback(self) -> dict:
        """Groww retry + historical close if LTP unavailable."""
        try:
            if not self._token:
                self._token = self.get_groww_token()
            if self._token:
                live = self.get_live_price()
                if live.get('price', 0) > 0:
                    return live
        except Exception:
            pass
        try:
            from src.groww_historical import fetch_latest_price
            p = fetch_latest_price(self._token or '')
            if p > 0:
                return {'price': p, 'volume': 1000, 'source': 'GROWW_HIST'}
        except Exception:
            pass
        # Final fallback: yfinance (free, slight delay — better than nothing)
        try:
            import yfinance as yf
            h = yf.Ticker('^NSEBANK').history(period='1d', interval='1m')
            if len(h) > 0:
                p = float(h['Close'].iloc[-1])
                v = int(h['Volume'].iloc[-1])
                if p > 0:
                    return {'price': p, 'volume': v, 'source': 'YFINANCE_FALLBACK'}
        except Exception:
            pass
        return {}

    def _seed_candles_from_history(self):
        """Cold start: seed 1m candles from Groww historical API."""
        if len(self.b5.get_candles()) >= 10:
            return
        try:
            from src.groww_historical import seed_candle_builders
            count = seed_candle_builders(self.b1, self.b5, self.b15, self._token or '')
            if count > 0:
                print(f"📈 Groww historical: seeded {count} x 1m candles")
        except Exception as e:
            STATE.add_error(f"Groww candle seed: {str(e)[:40]}")

    def _calc_rsi(self, candles, period=14) -> float:
        if len(candles) < period+1: return 50.0
        closes = [c['close'] for c in candles[-(period+1):]]
        gains  = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
        losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
        ag, al = sum(gains)/period, sum(losses)/period
        return round(100-(100/(1+ag/al)) if al>0 else 100, 1)

    def _calc_vwap(self, candles) -> float:
        pv = sum(c['close']*c['volume'] for c in candles if c['volume']>0)
        tv = sum(c['volume'] for c in candles if c['volume']>0)
        return round(pv/tv, 2) if tv > 0 else 0

    def _calc_atr(self, candles, period=14) -> float:
        if len(candles) < period: return 500.0
        return round(sum(c['high']-c['low'] for c in candles[-period:])/period, 0)

    def _publish(self, price: float, volume: int, source: str):
        now = datetime.now(IST)

        # Feed ALL THREE timeframes
        self.b1.add_tick(price, volume, now)
        self.b5.add_tick(price, volume, now)
        self.b15.add_tick(price, volume, now)

        c1  = self.b1.get_candles()
        c5  = self.b5.get_candles()
        c15 = self.b15.get_candles()

        regime = 'TRENDING'
        if len(c5) >= 10:
            closes = [c['close'] for c in c5]
            highs  = [c['high'] for c in c5]
            lows   = [c['low'] for c in c5]
            day_range = max(highs) - min(lows)
            avg_price = sum(closes) / len(closes)
            if avg_price > 0 and day_range > 0:
                range_pct = day_range / avg_price * 100
                efficiency = abs(closes[-1] - closes[0]) / day_range
                if range_pct < 0.4:
                    regime = 'TIGHT_RANGE'
                elif efficiency < 0.35:
                    regime = 'RANGING'

        # Session
        t = now.time()
        if   t < dtime(9,15):  sess = 'PRE_MARKET'
        elif t < dtime(9,45):  sess = 'OPEN_VOLATILE'
        elif t < dtime(11,30): sess = 'MORNING_TREND'
        elif t < dtime(13,0):  sess = 'LUNCH_CHOP'
        elif t < dtime(14,30): sess = 'AFTERNOON_MOVE'
        elif t < dtime(15,30): sess = 'EOD_CHOP'
        else:                   sess = 'CLOSED'

        STATE.update('market', {
            'price':        round(price, 2),
            'vwap':         self._calc_vwap(c1),
            'rsi_1m':       self._calc_rsi(c1, 9),    # 1-min RSI
            'rsi_5m':       self._calc_rsi(c5, 14),   # 5-min RSI
            'rsi_15m':      self._calc_rsi(c15, 14),  # 15-min RSI
            'atr':          self._calc_atr(c5),
            'session':      sess,
            'regime':       regime,
            'candles_1m':   c1[-60:],
            'candles_5m':   c5[-50:],
            'candles_15m':  c15[-30:],  # NEW ✅
            'updated_at':   now.strftime('%H:%M:%S'),
            'data_source':  source,
            'connected':    True,
        })
        STATE.set('system.market_open',
                  dtime(9,15) <= t <= dtime(15,30))
        STATE.heartbeat()
        try:
            from src.safety import update_heartbeat
            update_heartbeat()
        except Exception:
            pass

    def _active_watch_on_price(self):
        """On each BNF tick: re-price virtual orders (real uses Monitor + smart MTM)."""
        try:
            from src.position_watch import watch_mode_active
            if not watch_mode_active():
                return
            from src.shadow_learning import has_open_virtual_orders, tick_shadow_trades
            if has_open_virtual_orders():
                tick_shadow_trades()
        except Exception:
            pass

    def run(self):
        STATE.set_agent_status('data', 'RUNNING')
        print("📡 Data Agent: 1-min + 5-min + 15-min candles ✅")
        try:
            from src.groww_auth import is_rate_limited, rate_limit_remaining_sec
            if is_rate_limited():
                print(
                    f"⏸️ Groww cooldown {rate_limit_remaining_sec() // 60}m — "
                    f"price via yfinance until token cache refreshes"
                )
                self._token = self.get_groww_token()  # returns stale cache if any
            else:
                self._token = self.get_groww_token()
        except Exception as e:
            print(f"⚠️ Groww token skipped: {str(e)[:80]}")
            self._token = STATE.get('system.groww_token', '') or ''
        self._seed_candles_from_history()

        while self.running and STATE.get('system.running'):
            try:
                self.refresh_token_if_needed()
                if not self.is_market_time():
                    time.sleep(120)
                    continue

                from src.api_scheduler import should_fetch
                result = self.get_live_price()
                if not result:
                    result = self.get_price_fallback()

                if result and result.get('price', 0) > 0:
                    from src.api_scheduler import mark_fetched
                    mark_fetched('groww_ltp')
                    self._publish(result['price'],
                                  result.get('volume', 1000),
                                  result.get('source', 'UNKNOWN'))
                    self._active_watch_on_price()
                    self._ctx_tick += 1
                    if self._ctx_tick % 6 == 1 and should_fetch('market_context'):
                        try:
                            from src.market_context import refresh_market_context
                            refresh_market_context(self._token or '')
                            from src.api_scheduler import mark_fetched
                            mark_fetched('market_context')
                            from src.market_flow import refresh_market_flow
                            zone = STATE.get('zone') or {}
                            if should_fetch('market_flow'):
                                refresh_market_flow(zone.get('bias', 'BULLISH'))
                                mark_fetched('market_flow')
                        except Exception:
                            pass
                    from src.position_watch import bnf_poll_interval_sec
                    from src.groww_feed_store import is_feed_live
                    if is_feed_live():
                        time.sleep(int(os.getenv('BNF_POLL_FEED_IDLE_SEC', '20')))
                    else:
                        time.sleep(bnf_poll_interval_sec())
                else:
                    STATE.set('market.connected', False)
                    time.sleep(30)

            except Exception as e:
                STATE.add_error(f"Data Agent: {str(e)[:60]}")
                time.sleep(15)

        STATE.set_agent_status('data', 'STOPPED')
