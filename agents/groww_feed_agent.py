"""Groww WebSocket feed agent — live BNF + option LTP via GrowwFeed."""

import os
import time
import threading
from datetime import datetime, time as dtime
import pytz

from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')
GROWW_FEED_ENABLED = os.getenv('GROWW_FEED_ENABLED', 'true').lower() == 'true'
RESYNC_SEC = int(os.getenv('GROWW_FEED_RESYNC_SEC', '20'))


class GrowwFeedAgent(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name='GrowwFeedAgent')
        self._feed = None
        self._groww = None
        self._subscribed: list = []
        self._sub_key = ''
        self._lock = threading.Lock()

    def _market_window(self) -> bool:
        t = datetime.now(IST).time()
        return dtime(8, 55) <= t <= dtime(15, 35)

    def _get_token(self) -> str:
        tok = STATE.get('system.groww_token', '')
        if tok and tok.startswith('eyJ'):
            return tok
        try:
            from src.groww_auth import fetch_groww_token
            tok = fetch_groww_token(max_retries=1, base_delay_sec=60)
            if tok:
                STATE.set('system.groww_token', tok)
            return tok or ''
        except Exception:
            return os.getenv('GROWW_ACCESS_TOKEN', '')

    def _on_ltp(self, _meta=None):
        try:
            if not self._feed:
                return
            payload = self._feed.get_ltp()
            from src.groww_feed_store import ingest_ltp_payload, run_position_watch
            n = ingest_ltp_payload(payload)
            if n > 0:
                run_position_watch()
        except Exception as e:
            STATE.add_error(f'Feed tick: {str(e)[:40]}')

    def _build_subscription_list(self) -> list:
        from src.groww_instruments import bnf_feed_instrument, option_feed_instrument
        if not self._groww:
            return []

        instruments = []
        bnf = bnf_feed_instrument(self._groww)
        if bnf.get('exchange_token'):
            instruments.append(bnf)

        seen = {i['exchange_token'] for i in instruments}

        if STATE.get('position.open'):
            pos = STATE.get('position', {})
            opt = option_feed_instrument(
                self._groww,
                pos.get('strike', 0),
                pos.get('opt_type', 'CE'),
                pos.get('expiry', ''),
            )
            if opt.get('exchange_token') and opt['exchange_token'] not in seen:
                instruments.append(opt)
                seen.add(opt['exchange_token'])

        try:
            from src.shadow_learning import get_open_virtual_positions
            for row in get_open_virtual_positions():
                opt = option_feed_instrument(
                    self._groww, row['strike'], row['opt_type'], row['expiry'],
                )
                if opt.get('exchange_token') and opt['exchange_token'] not in seen:
                    instruments.append(opt)
                    seen.add(opt['exchange_token'])
        except Exception:
            pass

        return instruments

    def _ensure_feed(self) -> bool:
        token = self._get_token()
        if not token:
            from src.groww_feed_store import mark_disconnected
            mark_disconnected()
            return False

        try:
            from growwapi import GrowwAPI, GrowwFeed
            from src.groww_client import get_groww_client
            self._groww = get_groww_client(token)
            if self._feed is None:
                self._feed = GrowwFeed(self._groww)
                STATE.set('system.groww_feed', 'CONNECTED')
                print('📡 Groww WebSocket feed connected')
            return True
        except Exception as e:
            from src.groww_feed_store import mark_disconnected
            mark_disconnected()
            STATE.set('system.groww_feed', f'ERROR: {str(e)[:40]}')
            self._feed = None
            return False

    def _sync_subscriptions(self):
        if not self._feed:
            return

        instruments = self._build_subscription_list()
        if not instruments:
            return

        key = '|'.join(sorted(
            f"{i.get('exchange')}:{i.get('segment')}:{i.get('exchange_token')}"
            for i in instruments
        ))
        if key == self._sub_key:
            return

        with self._lock:
            try:
                if self._subscribed:
                    self._feed.unsubscribe_ltp(self._subscribed)
            except Exception:
                pass

            self._feed.subscribe_ltp(instruments, on_data_received=self._on_ltp)
            self._subscribed = list(instruments)
            self._sub_key = key
            print(f'📡 Feed subscribed: {len(instruments)} instruments (BNF + options)')

    def run(self):
        STATE.set_agent_status('groww_feed', 'RUNNING')
        print('📡 Groww Feed Agent: WebSocket LTP stream ✅')

        while STATE.get('system.running'):
            try:
                if not GROWW_FEED_ENABLED:
                    time.sleep(60)
                    continue

                if not self._market_window():
                    if self._feed:
                        self._sub_key = ''
                        self._subscribed = []
                    from src.groww_feed_store import mark_disconnected
                    mark_disconnected()
                    STATE.set('system.groww_feed', 'CLOSED')
                    time.sleep(60)
                    continue

                if self._ensure_feed():
                    self._sync_subscriptions()
                    from src.groww_feed_store import is_feed_live, feed_status
                    if not is_feed_live() and feed_status()['last_tick_ago'] > 60:
                        STATE.set('system.groww_feed', 'WAITING_TICKS')

            except Exception as e:
                STATE.add_error(f'GrowwFeed: {str(e)[:50]}')
                self._feed = None
                self._sub_key = ''

            time.sleep(RESYNC_SEC)

        STATE.set_agent_status('groww_feed', 'STOPPED')
