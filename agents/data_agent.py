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

    def is_market_time(self) -> bool:
        now = datetime.now(IST).time()
        return dtime(9, 0) <= now <= dtime(15, 50)

    def get_groww_token(self) -> str:
        try:
            import pyotp
            from growwapi import GrowwAPI
            secret = os.getenv('GROWW_TOTP_SECRET', '')
            token  = os.getenv('GROWW_TOTP_TOKEN',  '')
            if secret and token:
                totp = pyotp.TOTP(secret).now()
                access_token = GrowwAPI.get_access_token(api_key=token, totp=totp)
                # Store in STATE so Execution Agent can use it
                STATE.set('system.groww_token', access_token)
                return access_token
        except Exception as e:
            STATE.add_error(f"Token: {str(e)[:40]}")
        return os.getenv('GROWW_ACCESS_TOKEN', '')

    def refresh_token_if_needed(self):
        """Auto-refresh token at 8:45 AM and every 4 hours during market hours"""
        now = datetime.now(IST)
        hour = now.hour
        
        # Scheduled refresh: 8:45 AM + every 4 hours (12:45, 3:45 PM)
        if (hour == 8 and 44 <= now.minute <= 46) or \
           (hour == 12 and 44 <= now.minute <= 46) or \
           (hour == 15 and 44 <= now.minute <= 46):
            self._token = self.get_groww_token()
            STATE.set('system.token_status', f'REFRESHED_{hour}:45AM')
            print(f"🔑 Token refreshed at {hour}:45")

    def get_live_price(self) -> dict:
        """Get live BankNifty price from Groww with auto-retry on token expiry"""
        try:
            if not self._token:
                self._token = self.get_groww_token()
            from growwapi import GrowwAPI
            groww = GrowwAPI(self._token)
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
            # Detect authentication errors and refresh token automatically
            error_str = str(e).lower()
            if 'auth' in error_str or 'expired' in error_str or 'invalid' in error_str:
                print(f"🔄 Token expired detected: {str(e)[:40]}")
                self._token = self.get_groww_token()  # Auto-refresh
                # Retry once with new token
                try:
                    from growwapi import GrowwAPI
                    groww = GrowwAPI(self._token)
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

    def get_price_yfinance(self) -> dict:
        """Retry Groww with fresh token on timeout"""
        try:
            # Refresh token and retry Groww
            self._token = self.get_groww_token()
            if self._token:
                return self.get_live_price()
        except:
            pass
        return {}

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

    def run(self):
        STATE.set_agent_status('data', 'RUNNING')
        print("📡 Data Agent: 1-min + 5-min + 15-min candles ✅")
        self._token = self.get_groww_token()

        while self.running and STATE.get('system.running'):
            try:
                self.refresh_token_if_needed()
                if not self.is_market_time():
                    time.sleep(30); continue

                result = self.get_live_price()
                if not result:
                    result = self.get_price_yfinance()

                if result and result.get('price', 0) > 0:
                    self._publish(result['price'],
                                  result.get('volume', 1000),
                                  result.get('source', 'UNKNOWN'))
                    time.sleep(10)
                else:
                    STATE.set('market.connected', False)
                    time.sleep(30)

            except Exception as e:
                STATE.add_error(f"Data Agent: {str(e)[:60]}")
                time.sleep(15)

        STATE.set_agent_status('data', 'STOPPED')
