#!/usr/bin/env python3
"""Test Groww TOTP Authentication Flow"""

import os
import sys
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

IST = pytz.timezone('Asia/Kolkata')

def test_totp_generation():
    print("=" * 60)
    print("TEST 1: TOTP Generation")
    print("=" * 60)
    try:
        import pyotp
        secret = os.getenv('GROWW_TOTP_SECRET', '')
        if not secret:
            print("FAIL: GROWW_TOTP_SECRET not set")
            return False
        print("PASS: Secret found")
        totp = pyotp.TOTP(secret)
        code = totp.now()
        print(f"PASS: TOTP code generated: {code}")
        return True
    except Exception as e:
        print(f"FAIL: TOTP generation failed: {e}")
        return False

def test_groww_token_generation():
    print("\n" + "=" * 60)
    print("TEST 2: Groww Token Generation (TOTP to Access Token)")
    print("=" * 60)
    try:
        import pyotp
        from growwapi import GrowwAPI
        secret = os.getenv('GROWW_TOTP_SECRET', '')
        totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
        if not secret or not totp_token:
            print("FAIL: Missing credentials")
            return None
        print("PASS: Credentials loaded")
        totp = pyotp.TOTP(secret)
        code = totp.now()
        print(f"PASS: TOTP code: {code}")
        print("Calling GrowwAPI.get_access_token()...")
        access_token = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
        if access_token:
            print(f"PASS: Access token obtained!")
            print(f"Token: {access_token[:30]}...{access_token[-30:]}")
            return access_token
        else:
            print("FAIL: No token returned")
            return None
    except Exception as e:
        print(f"FAIL: Token generation failed: {e}")
        return None

def test_groww_api_fetch(token):
    print("\n" + "=" * 60)
    print("TEST 3: Groww API Fetch (Get BNF Price)")
    print("=" * 60)
    if not token:
        print("FAIL: No token provided")
        return False
    try:
        from growwapi import GrowwAPI
        print("PASS: Creating GrowwAPI instance...")
        api = GrowwAPI(token)
        print("Fetching BNF (NIFTY BANK) price...")
        q = api.get_ltp(
            exchange_trading_symbols=('NIFTY BANK',),
            segment=api.SEGMENT_CASH
        )
        print("PASS: Response received!")
        if q and isinstance(q, dict):
            prices = q.get('ltps', [])
            if prices:
                price = prices[0].get('ltp', 0) or prices[0].get('last_price', 0)
                print(f"\nPASS: BNF Price: {price}")
                return True
            else:
                print("FAIL: No price data")
                return False
        else:
            print("FAIL: Invalid response")
            return False
    except Exception as e:
        print(f"FAIL: API fetch failed: {e}")
        return False

def test_bot_uses_totp():
    print("\n" + "=" * 60)
    print("TEST 4: Bot Code Verification")
    print("=" * 60)
    try:
        with open('agents/data_agent.py', 'r') as f:
            data_agent = f.read()
        checks = {
            'TOTP import': 'import pyotp' in data_agent,
            'TOTP generation': 'pyotp.TOTP' in data_agent,
            'Groww token fetch': 'get_groww_token' in data_agent,
            'Token in STATE': "STATE.set('system.groww_token'" in data_agent,
            'Groww API call': 'GrowwAPI' in data_agent,
            'Correct LTP params': 'exchange_trading_symbols=' in data_agent,
        }
        all_good = True
        for check, result in checks.items():
            status = "PASS" if result else "FAIL"
            print(f"{status}: {check}")
            if not result:
                all_good = False
        with open('agents/agents.py', 'r') as f:
            agents = f.read()
        has_state_token = "STATE.get('system.groww_token'" in agents
        print(f"\n{'PASS' if has_state_token else 'FAIL'}: Execution Agent reads token from STATE")
        return all_good and has_state_token
    except Exception as e:
        print(f"FAIL: Verification failed: {e}")
        return False

def main():
    print("\nGROWW TOTP AUTHENTICATION TEST SUITE\n")
    results = {}
    results['TOTP Generation'] = test_totp_generation()
    token = test_groww_token_generation()
    results['Token Generation'] = token is not None
    if token:
        results['API Fetch'] = test_groww_api_fetch(token)
    else:
        print("\nSkipping API fetch (no token)")
        results['API Fetch'] = None
    results['Bot Code'] = test_bot_uses_totp()
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for test, result in results.items():
        if result is True:
            status = "PASS"
        elif result is False:
            status = "FAIL"
        else:
            status = "SKIP"
        print(f"{status} - {test}")
    passed = sum(1 for r in results.values() if r is True)
    total = len(results)
    print(f"\nResult: {passed}/{total} tests passed")
    if results.get('TOTP Generation') and results.get('Token Generation'):
        print("\nTOTP flow is WORKING")
        print("Bot can use Groww API with TOTP authentication")
        if results.get('API Fetch'):
            print("Live price fetching from Groww API verified")
    else:
        print("\nTOTP flow has issues - check credentials")

if __name__ == '__main__':
    main()
