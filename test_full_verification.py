#!/usr/bin/env python3
"""Comprehensive verification of bot integration"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, '/Users/manikandan.palanisamy/Downloads/bnf-bot-fixed 2/bnf-bot')

from agents.data_agent import DataAgent, BANKNIFTY_SYMBOL
from core.shared_state import STATE
import pyotp

print("\n" + "="*70)
print("🔍 COMPREHENSIVE BOT VERIFICATION")
print("="*70)

tests_passed = 0
tests_failed = 0

# Test 1: Symbol Configuration
print("\n[1/5] Symbol Configuration")
try:
    assert BANKNIFTY_SYMBOL == "NSE_BANKNIFTY", f"Wrong symbol: {BANKNIFTY_SYMBOL}"
    print(f"  ✅ Symbol: {BANKNIFTY_SYMBOL}")
    tests_passed += 1
except AssertionError as e:
    print(f"  ❌ {e}")
    tests_failed += 1

# Test 2: TOTP Generation
print("\n[2/5] TOTP Generation")
try:
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert len(code) == 6, f"Invalid code length: {len(code)}"
    assert code.isdigit(), f"Code not numeric: {code}"
    print(f"  ✅ TOTP Code: {code}")
    tests_passed += 1
except Exception as e:
    print(f"  ❌ {e}")
    tests_failed += 1

# Test 3: Token Generation
print("\n[3/5] Token Generation")
try:
    data_agent = DataAgent()
    token = data_agent.get_groww_token()
    assert token, "No token returned"
    assert token.startswith('eyJ'), "Token doesn't look like JWT"
    assert len(token) > 100, "Token too short"
    print(f"  ✅ Token: {token[:40]}...{token[-20:]}")
    tests_passed += 1
except Exception as e:
    print(f"  ❌ {e}")
    tests_failed += 1

# Test 4: STATE Storage
print("\n[4/5] STATE Storage")
try:
    stored_token = STATE.get('system.groww_token', '')
    assert stored_token, "Token not in STATE"
    assert stored_token == token, "Stored token doesn't match generated token"
    print(f"  ✅ Token stored in STATE")
    tests_passed += 1
except Exception as e:
    print(f"  ❌ {e}")
    tests_failed += 1

# Test 5: Price Fetching
print("\n[5/5] Price Fetching")
try:
    price_data = data_agent.get_live_price()
    assert price_data, "No price data returned"
    assert 'price' in price_data, "Missing price key"
    assert 'source' in price_data, "Missing source key"
    assert price_data['price'] > 0, f"Invalid price: {price_data['price']}"
    assert price_data['source'] == 'GROWW', f"Wrong source: {price_data['source']}"
    print(f"  ✅ BankNifty Price: ₹{price_data['price']:,.2f}")
    print(f"  ✅ Source: {price_data['source']}")
    tests_passed += 1
except Exception as e:
    print(f"  ❌ {e}")
    tests_failed += 1

# Summary
print("\n" + "="*70)
print(f"📊 RESULTS: {tests_passed}/5 tests passed")
if tests_failed == 0:
    print("✅✅✅ BOT IS READY FOR TRADING ✅✅✅")
else:
    print(f"⚠️  {tests_failed} test(s) failed")
print("="*70 + "\n")

sys.exit(0 if tests_failed == 0 else 1)
