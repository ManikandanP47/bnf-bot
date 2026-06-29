"""
Groww F&O Trader
Executes BankNifty option orders via Groww API
Supports: Buy CE/PE, OCO (target + SL together), positions
"""

import os
import json
import logging
from datetime import datetime
import pytz

try:
    from growwapi import GrowwAPI
    GROWW_AVAILABLE = True
except ImportError:
    GROWW_AVAILABLE = False

IST    = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)

JOURNAL = 'journal.json'


from src.groww_symbols import groww_option_symbol


class GrowwTrader:

    def __init__(self, token: str = ''):
        self.token = token or os.getenv('GROWW_ACCESS_TOKEN', '')
        self.paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
        self.groww = None
        self.connect_error = ''

        if GROWW_AVAILABLE and self.token and not self.paper:
            try:
                from src.groww_client import get_groww_client
                self.groww = get_groww_client(self.token)
                logger.info("Groww live mode connected")
            except Exception as e:
                self.connect_error = str(e)
                logger.error(f"Groww connect failed: {e}")
                self.groww = None

    def get_contract_id(self, index: str, strike: int,
                        opt_type: str, expiry: str) -> str:
        """Groww FNO symbol e.g. NSE_BANKNIFTY26JUL58200CE"""
        try:
            return groww_option_symbol(index, strike, opt_type, expiry)
        except Exception as e:
            logger.error(f"Contract ID error: {e}")
            return f"NSE_{index}{strike}{opt_type}"

    def buy_option(self, index: str, strike: int,
                   opt_type: str, expiry: str,
                   sl_prem: float, tgt_prem: float,
                   lots: int = 1) -> dict:
        """
        Buy option + place OCO (target + SL together)
        One call = entry + exit management
        """
        qty         = lots * 15  # 1 lot = 15 units
        contract_id = self.get_contract_id(index, strike, opt_type, expiry)

        if self.paper:
            return self._paper_execute(
                contract_id, qty, sl_prem, tgt_prem, expiry
            )

        if not self.groww:
            return {
                'success': False,
                'error': (
                    'Groww API not connected — live buy blocked. '
                    f'{self.connect_error[:80] if self.connect_error else "Check token / network."}'
                ),
            }

        try:
            # Step 1: Buy at market
            buy_resp = self.groww.place_order(
                trading_symbol   = contract_id,
                exchange         = 'NSE',
                segment          = self.groww.SEGMENT_FNO,
                transaction_type = 'BUY',
                order_type       = 'MARKET',
                quantity         = qty,
                product          = 'NRML'
            )

            if buy_resp.get('status') != 'success':
                return {
                    'success': False,
                    'error':   buy_resp.get('message', 'Buy order failed')
                }

            order_id = buy_resp.get('groww_order_id', 'N/A')

            # Step 2: OCO order (SL + Target together)
            # When target hits → SL cancels automatically
            oco_resp = self.groww.place_smart_order(
                trading_symbol   = contract_id,
                exchange         = 'NSE',
                smart_order_type = 'OCO',
                segment          = self.groww.SEGMENT_FNO,
                quantity         = qty,
                product          = 'NRML',
                # Target leg
                target_price     = tgt_prem,
                target_order_type = 'LIMIT',
                # SL leg
                stop_loss_price  = sl_prem,
                stop_loss_order_type = 'MARKET'
            )

            oco_ok = False
            oco_id = ''
            if oco_resp.get('status') == 'success':
                oco_ok = True
                oco_id = oco_resp.get('groww_order_id', oco_resp.get('smart_order_id', ''))
            else:
                logger.warning(f"OCO placement failed: {oco_resp}")

            trade = {
                'name':        f"{index} {strike} {opt_type}",
                'contract_id': contract_id,
                'entry_id':    order_id,
                'oco_id':      oco_id,
                'oco_ok':      oco_ok,
                'sl_prem':     sl_prem,
                'tgt_prem':    tgt_prem,
                'qty':         qty,
                'strike':      strike,
                'opt_type':    opt_type,
                'expiry':      expiry,
                'time':        datetime.now(IST).strftime('%d %b %Y %I:%M %p'),
                'status':      'OPEN',
                'mode':        'LIVE'
            }
            self._log(trade)

            msg = f"Bought {contract_id}"
            if oco_ok:
                msg += " + OCO placed ✅"
            else:
                msg += " ⚠️ OCO failed — monitor will manage exit"

            return {
                'success':  True,
                'order_id': order_id,
                'oco_ok':   oco_ok,
                'oco_id':   oco_id,
                'contract_id': contract_id,
                'message':  msg,
                'trade':    trade
            }

        except Exception as e:
            logger.error(f"Groww execution error: {e}")
            return {'success': False, 'error': str(e)}

    def _paper_execute(self, contract_id, qty,
                       sl_prem, tgt_prem, expiry) -> dict:
        """Simulate trade — no real money"""
        trade = {
            'name':        contract_id,
            'contract_id': contract_id,
            'entry_id':    f"PAPER_{len(self._read_journal())+1:04d}",
            'sl_prem':     sl_prem,
            'tgt_prem':    tgt_prem,
            'qty':         qty,
            'time':        datetime.now(IST).strftime('%d %b %Y %I:%M %p'),
            'status':      'OPEN',
            'mode':        'PAPER'
        }
        self._log(trade)
        return {
            'success':  True,
            'order_id': trade['entry_id'],
            'message':  f"Paper trade: {contract_id}",
            'trade':    trade,
            'paper':    True
        }

    def sell_option(self, contract_id: str, qty: int,
                    reason: str = 'EXIT') -> dict:
        """Market sell — cancels smart orders first, then sells."""
        if self.paper:
            return {'success': True, 'paper': True, 'qty': qty}
        if not self.groww:
            return {
                'success': False,
                'paper': False,
                'qty': qty,
                'error': (
                    'Groww API not connected — live sell blocked. '
                    f'{self.connect_error[:80] if self.connect_error else "Close manually on Groww."}'
                ),
            }

        try:
            self._cancel_smart_orders(contract_id)

            sell_resp = self.groww.place_order(
                trading_symbol   = contract_id,
                exchange         = 'NSE',
                segment          = self.groww.SEGMENT_FNO,
                transaction_type = 'SELL',
                order_type       = 'MARKET',
                quantity         = qty,
                product          = 'NRML',
            )

            ok = sell_resp.get('status') == 'success'
            order_id = sell_resp.get('groww_order_id', '')
            if not ok:
                return {
                    'success': False,
                    'error':   sell_resp.get('message', 'Sell order failed'),
                }

            return {
                'success':  True,
                'order_id': order_id,
                'qty':      qty,
                'message':  f"Sold {qty} units {contract_id} ({reason})",
            }
        except Exception as e:
            logger.error(f"Sell error: {e}")
            return {'success': False, 'error': str(e)}

    def _cancel_smart_orders(self, contract_id: str):
        """Best-effort cancel OCO / open orders before manual exit."""
        if not self.groww:
            return
        try:
            if hasattr(self.groww, 'cancel_smart_order'):
                orders = self.groww.get_order_list(
                    segment=self.groww.SEGMENT_FNO
                ) if hasattr(self.groww, 'get_order_list') else {}
                for o in (orders.get('order_list', []) if isinstance(orders, dict) else []):
                    sym = o.get('trading_symbol', o.get('tradingSymbol', ''))
                    if sym == contract_id and o.get('status', '').upper() in (
                        'OPEN', 'PENDING', 'TRIGGER_PENDING', 'ACTIVE'
                    ):
                        oid = o.get('groww_order_id', o.get('order_id', ''))
                        if oid:
                            self.groww.cancel_order(order_id=oid)
        except Exception as e:
            logger.warning(f"Cancel orders: {e}")

    def sell_option_by_params(self, strike: int, opt_type: str,
                              expiry: str, qty: int,
                              index: str = 'BANKNIFTY',
                              reason: str = 'EXIT') -> dict:
        contract_id = self.get_contract_id(index, strike, opt_type, expiry)
        return self.sell_option(contract_id, qty, reason)

    def get_positions(self) -> list:
        if self.paper:
            return [t for t in self._read_journal() if t.get('status') == 'OPEN']
        try:
            resp = self.groww.get_positions(segment=self.groww.SEGMENT_FNO)
            return resp.get('position_list', [])
        except:
            return []

    def get_pnl(self) -> float:
        if self.paper:
            return 0.0
        try:
            positions = self.get_positions()
            return sum(float(p.get('pnl', 0)) for p in positions)
        except:
            return 0.0

    def _log(self, trade: dict):
        trades = self._read_journal()
        trades.append(trade)
        with open(JOURNAL, 'w') as f:
            json.dump(trades, f, indent=2, default=str)

    def _read_journal(self) -> list:
        try:
            if os.path.exists(JOURNAL):
                with open(JOURNAL) as f:
                    return json.load(f)
        except:
            pass
        return []
