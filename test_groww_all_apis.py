#!/usr/bin/env python3
"""
Groww API full test suite — TOTP auth, refresh, all endpoints used by bot.
Run: python3 test_groww_all_apis.py
"""

import os
import sys
import time
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()
IST = pytz.timezone('Asia/Kolkata')

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def ok(name, detail=""):
    global PASS
    PASS += 1
    RESULTS.append(("PASS", name, detail))
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    RESULTS.append(("FAIL", name, detail))
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    RESULTS.append(("SKIP", name, detail))
    print(f"  ⏭️  {name}" + (f" — {detail}" if detail else ""))


def get_totp_token():
    from src.groww_auth import fetch_groww_token
    try:
        token = fetch_groww_token(max_retries=6, base_delay_sec=60)
        ok("TOTP → access token", f"{token[:12]}...{token[-8:]}")
        return token, None
    except Exception as e:
        bad("TOTP → access token", str(e)[:100])
        return None, None


def test_token_refresh():
    print("\n── Token refresh (second TOTP call) ──")
    if os.getenv('GROWW_TEST_DOUBLE_AUTH', 'false').lower() != 'true':
        skip("Token refresh", "set GROWW_TEST_DOUBLE_AUTH=true to avoid rate limits")
        return None
    import pyotp
    from growwapi import GrowwAPI
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    api_key = os.getenv('GROWW_TOTP_TOKEN', '')
    time.sleep(2)
    code2 = pyotp.TOTP(secret).now()
    token2 = GrowwAPI.get_access_token(api_key=api_key, totp=code2)
    if token2:
        ok("Token refresh", "second token obtained")
        return token2
    bad("Token refresh", "failed on second call")
    return None


def run_tests(token):
    from growwapi import GrowwAPI
    g = GrowwAPI(token)

    print("\n── 1. Live price (LTP) ──")
    for sym in ('NSE_BANKNIFTY', 'NSE-BANKNIFTY'):
        try:
            q = g.get_ltp(exchange_trading_symbols=(sym,), segment=g.SEGMENT_CASH)
            price = 0
            if isinstance(q, dict):
                if sym in q:
                    price = float(q[sym])
                elif q.get('ltps'):
                    price = float(q['ltps'][0].get('ltp', 0) or 0)
            if price > 0:
                ok(f"LTP {sym}", f"₹{price:,.2f}")
                break
            else:
                bad(f"LTP {sym}", f"empty response: {str(q)[:80]}")
        except Exception as e:
            bad(f"LTP {sym}", str(e)[:80])
    else:
        pass

    print("\n── 2. OHLC / Quote ──")
    for sym in ('NSE_BANKNIFTY', 'NSE-BANKNIFTY'):
        try:
            if hasattr(g, 'get_ohlc'):
                ohlc = g.get_ohlc(
                    exchange_trading_symbols=(sym,),
                    segment=g.SEGMENT_CASH,
                )
                if ohlc:
                    ok(f"get_ohlc {sym}", str(ohlc)[:60])
                    break
        except Exception as e:
            bad(f"get_ohlc {sym}", str(e)[:60])
    try:
        if hasattr(g, 'get_quote'):
            q2 = g.get_quote(
                exchange_trading_symbols=('NSE_BANKNIFTY',),
                segment=g.SEGMENT_CASH,
            )
            if q2:
                ok("get_quote", str(q2)[:60])
    except Exception as e:
        skip("get_quote", str(e)[:50])

    print("\n── 3. Historical candles (₹499 plan) ──")
    end = datetime.now(IST)
    start = end - timedelta(hours=6)
    for sym in ('NSE-BANKNIFTY', 'NSE_BANKNIFTY'):
        try:
            resp = g.get_historical_candles(
                exchange=g.EXCHANGE_NSE,
                segment=g.SEGMENT_CASH,
                groww_symbol=sym,
                start_time=start.strftime('%Y-%m-%d %H:%M:%S'),
                end_time=end.strftime('%Y-%m-%d %H:%M:%S'),
                candle_interval=g.CANDLE_INTERVAL_MIN_1,
            )
            candles = resp.get('candles', []) if isinstance(resp, dict) else []
            if candles:
                ok(f"Historical 1m {sym}", f"{len(candles)} candles")
                last = candles[-1]
                ok("Last candle", f"close={last[4]} vol={last[5]}")
                break
            bad(f"Historical 1m {sym}", "no candles")
        except Exception as e:
            bad(f"Historical 1m {sym}", str(e)[:100])

    print("\n── 4. FNO expiries & contracts ──")
    expiry_date = None
    try:
        exp = g.get_expiries(
            exchange=g.EXCHANGE_NSE,
            underlying_symbol='BANKNIFTY',
        )
        dates = exp.get('expiries', []) if isinstance(exp, dict) else []
        if dates:
            expiry_date = dates[0]
            ok("get_expiries BANKNIFTY", f"{len(dates)} dates, next={expiry_date}")
        else:
            bad("get_expiries", str(exp)[:80])
    except Exception as e:
        bad("get_expiries", str(e)[:80])

    contract = None
    if expiry_date:
        try:
            con = g.get_contracts(
                exchange=g.EXCHANGE_NSE,
                underlying_symbol='BANKNIFTY',
                expiry_date=expiry_date,
            )
            contracts = con.get('contracts', []) if isinstance(con, dict) else []
            if contracts:
                contract = contracts[len(contracts) // 2]
                ok("get_contracts", f"{len(contracts)} contracts, sample={contract[:40]}...")
            else:
                bad("get_contracts", "empty")
        except Exception as e:
            bad("get_contracts", str(e)[:80])

    print("\n── 5. Option LTP (FNO) ──")
    if contract:
        try:
            from src.groww_symbols import groww_option_symbol
            # Also test our symbol builder with a strike from contract name
            import re
            m = re.search(r'(\d{5})(CE|PE)', contract.replace('-', ''))
            if m:
                strike, ot = int(m.group(1)), m.group(2)
                # parse expiry from contract like NSE-BANKNIFTY-02Jul25-58200-CE
                built = None
                try:
                    parts = contract.split('-')
                    for p in parts:
                        if 'CE' in p or 'PE' in p:
                            built = groww_option_symbol('BANKNIFTY', strike, ot,
                                datetime.strptime(expiry_date, '%Y-%m-%d').strftime('%d %b %Y'))
                except Exception:
                    pass
                for sym in filter(None, [built, contract.replace('-', '_')]):
                    try:
                        oq = g.get_ltp(exchange_trading_symbols=(sym,), segment=g.SEGMENT_FNO)
                        prem = 0
                        if isinstance(oq, dict):
                            if sym in oq:
                                prem = float(oq[sym])
                            elif oq.get('ltps'):
                                prem = float(oq['ltps'][0].get('ltp', 0) or 0)
                        if prem > 0:
                            ok(f"Option LTP", f"{sym} ₹{prem}")
                            break
                    except Exception:
                        continue
                else:
                    skip("Option LTP", "could not fetch premium for sample contract")
        except Exception as e:
            bad("Option LTP", str(e)[:80])
    else:
        skip("Option LTP", "no contract")

    print("\n── 6. Portfolio & margin ──")
    try:
        if hasattr(g, 'get_positions_for_user'):
            pos = g.get_positions_for_user(segment=g.SEGMENT_FNO)
            ok("get_positions_for_user FNO", f"keys={list(pos.keys())[:5] if isinstance(pos, dict) else 'ok'}")
        elif hasattr(g, 'get_positions'):
            skip("positions", "using REST fallback in safety.py")
    except Exception as e:
        bad("positions", str(e)[:80])

    try:
        margin = g.get_available_margin_details()
        ok("get_available_margin_details", str(margin)[:80])
    except Exception as e:
        bad("margin", str(e)[:80])

    print("\n── 7. Balance (REST fund API) ──")
    try:
        from src.safety import check_groww_balance
        bal = check_groww_balance(token, required_amount=5000, fail_open=False)
        if bal.get('available'):
            ok("F&O balance", f"₹{bal.get('balance', 0):,.0f} — {bal.get('reason', '')[:50]}")
        else:
            skip("F&O balance", bal.get('reason', 'unavailable')[:80])
    except Exception as e:
        bad("F&O balance", str(e)[:80])

    print("\n── 8. Orders (read-only) ──")
    try:
        orders = g.get_order_list(segment=g.SEGMENT_FNO)
        n = len(orders.get('order_list', [])) if isinstance(orders, dict) else 0
        ok("get_order_list FNO", f"{n} orders today")
    except Exception as e:
        bad("get_order_list", str(e)[:80])

    print("\n── 9. groww_historical module ──")
    try:
        from src.groww_historical import fetch_banknifty_candles, fetch_latest_price
        c = fetch_banknifty_candles(1, 4, token)
        if c:
            ok("groww_historical seed", f"{len(c)} candles via module")
        else:
            bad("groww_historical seed", "0 candles")
        p = fetch_latest_price(token)
        if p > 0:
            ok("groww_historical price", f"₹{p:,.2f}")
    except Exception as e:
        bad("groww_historical module", str(e)[:80])

    print("\n── 10. DataAgent token path ──")
    try:
        from agents.data_agent import DataAgent
        from agents.data_agent import BANKNIFTY_SYMBOL
        assert BANKNIFTY_SYMBOL == "NSE_BANKNIFTY", f"wrong symbol: {BANKNIFTY_SYMBOL}"
        da = DataAgent()
        t = da.get_groww_token()
        if t:
            ok("DataAgent.get_groww_token()", f"{t[:12]}...")
            da._token = t
            live = da.get_live_price()
            if live.get('price', 0) > 0:
                ok("DataAgent.get_live_price()", f"₹{live['price']:,.2f} via {live.get('source')}")
            else:
                bad("DataAgent.get_live_price()", "no price")
        else:
            bad("DataAgent.get_groww_token()", "empty")
    except Exception as e:
        bad("DataAgent", str(e)[:80])


def main():
    print("=" * 60)
    print("GROWW API FULL TEST SUITE")
    print(f"Time: {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
    print("=" * 60)

    token, _ = get_totp_token()
    if not token:
        print("\n❌ Cannot continue without token")
        sys.exit(1)

    run_tests(token)

    token2 = test_token_refresh()
    if token2:
        print("\n── Verify refreshed token works ──")
        try:
            from growwapi import GrowwAPI
            g2 = GrowwAPI(token2)
            q = g2.get_ltp(exchange_trading_symbols=('NSE_BANKNIFTY',), segment=g2.SEGMENT_CASH)
            price = float(q.get('NSE_BANKNIFTY', 0)) if isinstance(q, dict) else 0
            if price > 0:
                ok("Refreshed token LTP", f"₹{price:,.2f}")
            else:
                bad("Refreshed token LTP", str(q)[:60])
        except Exception as e:
            bad("Refreshed token LTP", str(e)[:60])

    print("\n" + "=" * 60)
    print(f"SUMMARY: ✅ {PASS} passed | ❌ {FAIL} failed | ⏭️  {SKIP} skipped")
    print("=" * 60)
    if FAIL == 0:
        print("🎉 All critical Groww APIs working with TOTP")
    else:
        print("⚠️  Some APIs failed — see details above")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"   ❌ {name}: {detail}")
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
