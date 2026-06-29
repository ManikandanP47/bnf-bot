#!/usr/bin/env python3
"""
Retry previously skipped/failed Groww + NSE OI tests with backoff.
Run: python3 test_retry_skipped.py
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def ok(label, detail=""):
    global PASS
    PASS += 1
    print(f"  ✅ {label}" + (f" — {detail}" if detail else ""))


def bad(label, detail=""):
    global FAIL
    FAIL += 1
    print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))


def main():
    print("=" * 60)
    print("RETRY: Groww + NSE OI (skipped/failed tests)")
    print("=" * 60)
    sys.stdout.flush()

    print("\n1. Groww auth (cache + backoff up to ~6 min)...")
    token = None
    try:
        from src.groww_auth import fetch_groww_token
        token = fetch_groww_token(max_retries=6, base_delay_sec=60)
        ok("Groww auth", f"{token[:20]}...")
    except Exception as e:
        bad("Groww auth", str(e)[:120])
        print("\nCannot continue Groww tests without token.")
    else:
        print("\n2. Live BankNifty price...")
        try:
            from agents.data_agent import DataAgent, BANKNIFTY_SYMBOL
            da = DataAgent()
            da._token = token
            px = da.get_live_price()
            if px and px.get('price', 0) > 0:
                ok("LTP", f"₹{px['price']:,.0f} ({px.get('source')})")
            else:
                bad("LTP", "empty")
        except Exception as e:
            bad("LTP", str(e)[:80])

        print("\n3. Historical candles...")
        try:
            from src.groww_historical import fetch_candles
            bars = fetch_candles(token, '15m', days=2)
            if bars and len(bars) >= 5:
                ok("Historical", f"{len(bars)} bars")
            else:
                bad("Historical", f"only {len(bars) if bars else 0} bars")
        except Exception as e:
            bad("Historical", str(e)[:80])

        print("\n4. Margin + positions...")
        try:
            from src.groww_client import get_groww_client
            g = get_groww_client(token)
            margin = g.get_available_margin_details()
            ok("Margin", str(margin)[:70])
            pos = g.get_positions_for_user(segment=g.SEGMENT_FNO)
            n = len((pos or {}).get('positions', []))
            ok("Positions", f"{n} open")
        except Exception as e:
            bad("Margin/positions", str(e)[:80])

    print("\n5. NSE OI (max pain, PCR)...")
    try:
        from src.oi_analysis import get_oi_data, calculate_max_pain
        raw = get_oi_data()
        if raw:
            mp = calculate_max_pain(raw)
            if mp.get('available'):
                ok("NSE OI", f"PCR={mp.get('pcr')} max_pain={mp.get('max_pain'):,}")
            else:
                bad("NSE OI", "parse failed")
        else:
            bad("NSE OI", "NSE returned no data (blocked or closed)")
    except Exception as e:
        bad("NSE OI", str(e)[:80])

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"RESULT: ✅ {PASS}/{total} passed | ❌ {FAIL} failed")
    print("=" * 60)

    if token and FAIL == 0:
        print("\n6. Full Groww suite (single auth via cache)...")
        os.environ['GROWW_TEST_DOUBLE_AUTH'] = 'false'
        rc = os.system(f'{sys.executable} {os.path.join(ROOT, "test_groww_all_apis.py")}')
        return rc if rc != 0 else 0

    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
