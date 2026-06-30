"""Sim Learning Agent — autonomous virtual CE/PE on live market flow."""

import threading
import time
import os
import requests
from datetime import datetime, time as dtime
import pytz

from core.shared_state import STATE
from core.messenger     import Messenger
from agents.learning_agent import BRAIN

IST = pytz.timezone('Asia/Kolkata')
BLOCK_ON_FILTER_ERROR = os.getenv('BLOCK_ON_FILTER_ERROR', 'true').lower() == 'true'

class SimLearningAgent(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name='SimLearningAgent')

    def run(self):
        STATE.set_agent_status('sim', 'RUNNING')
        print("🎮 Sim Learning Agent: virtual trades on live flow ✅")

        while STATE.get('system.running'):
            try:
                if STATE.get('system.market_open') and not STATE.get('system.paused'):
                    try:
                        from src.option_greeks import refresh_chain_snapshot
                        refresh_chain_snapshot()
                    except Exception:
                        pass
                    from src.market_simulator import scan_and_maybe_open
                    result = scan_and_maybe_open()
                    if result.get('opened'):
                        print(f"🎮 Sim opened #{result.get('id')}: {result.get('name')}")
                        try:
                            from src.structured_log import log_event
                            log_event('SimLearning', 'SIM_OPEN', id=result.get('id'))
                        except Exception:
                            pass
                    elif result.get('scanned'):
                        reason = result.get('reason', '')[:40]
                        if reason and 'cooldown' not in reason:
                            pass  # logged to sim_scan_log + JSONL
            except Exception as e:
                STATE.add_error(f"Sim: {str(e)[:50]}")
                try:
                    from src.sim_evidence import record_evidence
                    record_evidence('SIM_AGENT_ERROR', {'error': str(e)[:120]})
                except Exception:
                    pass
            time.sleep(60)

        STATE.set_agent_status('sim', 'STOPPED')
