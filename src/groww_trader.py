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


class GrowwTrader:

    def __init__(self):
        self.token     = os.getenv('GROWW_ACCESS_TOKEN', '')
        self.paper     = os.getenv('PAPER_MODE', 'true').lower() == 'true'
        self.groww     = None

        if GROWW_AVAILABLE and self.token and not self.paper:
            try:
                self.groww = GrowwAPI(self.token)
                logger.info("Groww live mode connected")
            except Exception as e:
                logger.error(f"Groww connect failed: {e}")
                self.paper = True

    def get_contract_id(self, index: str, strike: int,
                        opt_type: str, expiry: str) -> str:
        """
        Build Groww contract ID for BankNifty option
        Format: BANKNIFTY{YYMMDDD}{STRIKE}{CE/PE}
        Example: BANKNIFTY25071058300CE

        expiry format: '09 Jul 2026' → '250710'
        """
        try:
            dt       = datetime.strptime(expiry, '%d %b %Y')
            exp_code = dt.strftime('%y%m%d')  # e.g. 250710
            return f"{index}{exp_code}{strike}{opt_type}"
        except Exception as e:
            logger.error(f"Contract ID error: {e}")
            return f"BANKNIFTY{strike}{opt_type}"

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

            trade = {
                'name':        f"{index} {strike} {opt_type}",
                'contract_id': contract_id,
                'entry_id':    order_id,
                'sl_prem':     sl_prem,
                'tgt_prem':    tgt_prem,
                'qty':         qty,
                'time':        datetime.now(IST).strftime('%d %b %Y %I:%M %p'),
                'status':      'OPEN',
                'mode':        'LIVE'
            }
            self._log(trade)

            return {
                'success':  True,
                'order_id': order_id,
                'message':  f"Bought {contract_id} + OCO placed",
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
