"""
Shared State — The Nervous System
Every agent reads and writes here.
Thread-safe. Always consistent.
"""

import threading
import json
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


class SharedState:
    """
    Thread-safe shared state for all agents.
    One source of truth for the entire system.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._state = {

            # ── Market Data (updated by Data Agent) ───────────────
            'market': {
                'price':        0.0,
                'open':         0.0,
                'high':         0.0,
                'low':          0.0,
                'volume':       0,
                'trend':        'NEUTRAL',
                'regime':       'UNKNOWN',
                'session':      'CLOSED',
                'rsi':          50.0,
                'vwap':         0.0,
                'atr':          500.0,
                'candles_1m':   [],   # Last 60 one-min candles
                'candles_5m':   [],   # Last 50 five-min candles
                'updated_at':   '',
                'data_source':  'NONE',
                'connected':    False,
            },

            # ── Evening Zone (from daily scan) ────────────────────
            'zone': {
                'active':      False,
                'low':         0.0,
                'high':        0.0,
                'bias':        'NEUTRAL',
                'score':       0,
                'option_name': '',
                'strike':      0,
                'opt_type':    '',
                'expiry':      '',
                'premium':     0,
                'sl_prem':     0,
                'tgt_prem':    0,
                'saved_at':    '',
                'used':        False,
            },

            # ── Current Position ──────────────────────────────────
            'position': {
                'open':          False,
                'name':          '',
                'entry_price':   0.0,
                'entry_time':    '',
                'sl_prem':       0.0,
                'tgt_prem':      0.0,
                'trail_sl':      0.0,
                'peak_premium':  0.0,
                'leg1_done':     False,
                'leg1_profit':   0.0,
                'qty':           15,
                'strike':        0,
                'opt_type':      'CE',
                'expiry':        '',
                'contract_id':   '',
                'oco_ok':        False,
                'learning_id':   0,
                'bnf_at_entry':  0.0,
                'mae_rs':        0,
                'mfe_rs':        0,
            },

            # ── Signals between agents ────────────────────────────
            'signals': {
                'analysis_ready':      False,
                'analysis':            None,
                'risk_approved':       False,
                'risk':                None,
                'execute_now':         False,
                'awaiting_confirmation': False,
                'confirmation_sent':   False,
                'exit_now':            False,
                'exit_reason':         '',
            },

            # ── Brain (Learning Agent writes, all agents read) ─────
            'brain': {
                'min_score':          5,
                'max_trades_day':     1,
                'trades_today':       0,
                'total_trades':       0,
                'total_wins':         0,
                'win_rate':           0.0,
                'best_session':       '',
                'best_hour':          '',
                'avoid_hours':        [],
                'avoid_days':         [],
                'confidence_map':     {},
                'last_lesson':        '',
                'monthly_pnl':        {},
                'today_pnl':          0.0,
                'learning_stage':     'EARLY',
            },

            # ── System Health ─────────────────────────────────────
            'system': {
                'running':        True,
                'market_open':    False,
                'paused':         False,   # Manual pause via /pause command
                'pause_reason':   '',
                'last_heartbeat': '',
                'errors':         [],
                'weekly_losses':  0,       # Consecutive loss circuit breaker
                'week_start':     '',      # Track which week
                'week_pnl':       0.0,     # Cumulative week P&L for loss cap
                'agent_status': {
                    'data':      'STARTING',
                    'analysis':  'STARTING',
                    'risk':      'STARTING',
                    'execution': 'STARTING',
                    'monitor':   'STARTING',
                    'learning':  'STARTING',
                }
            }
        }

    def get(self, path: str, default=None):
        """Get value by dot-notation path: 'market.price'"""
        with self._lock:
            parts = path.split('.')
            obj   = self._state
            for p in parts:
                if isinstance(obj, dict):
                    obj = obj.get(p, default)
                else:
                    return default
            return obj

    def set(self, path: str, value):
        """Set value by dot-notation path"""
        with self._lock:
            parts = path.split('.')
            obj   = self._state
            for p in parts[:-1]:
                obj = obj.setdefault(p, {})
            obj[parts[-1]] = value

    def update(self, section: str, data: dict):
        """Update entire section atomically"""
        with self._lock:
            if section in self._state:
                self._state[section].update(data)
            else:
                self._state[section] = data

    def snapshot(self) -> dict:
        """Get full state snapshot"""
        with self._lock:
            import copy
            return copy.deepcopy(self._state)

    def set_agent_status(self, agent: str, status: str):
        with self._lock:
            self._state['system']['agent_status'][agent] = status

    def add_error(self, error: str):
        with self._lock:
            errors = self._state['system']['errors']
            errors.append(f"{datetime.now(IST).strftime('%H:%M')} {error}")
            self._state['system']['errors'] = errors[-20:]  # Keep last 20

    def heartbeat(self):
        with self._lock:
            self._state['system']['last_heartbeat'] = \
                datetime.now(IST).strftime('%d %b %H:%M:%S IST')


# Global shared state instance
STATE = SharedState()
