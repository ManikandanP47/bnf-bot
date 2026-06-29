#!/usr/bin/env python3
"""Test fetching BankNifty price with correct symbol"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("BankNifty Price Fetch Test")
print("="*60)

try:
    # Generate fresh token
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    
    print(f"TOTP Code: {code}")
    
    token = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
    print(f"✅ Access token obtained")
    
    api = GrowwAPI(token)
    
    # Fetch BankNifty price with CORRECT symbol and segment
    print(f"\nFetching BANKNIFTY price...")
    q = api.get_ltp(
        exchange_trading_symbols=('BANKNIFTY',),
        segment=api.SEGMENT_CASH
    )
    
    print(f"Response: {q}")
    
    if q and isinstance(q, dict):
        prices = q.get('ltps', [])
        if prices:
            price = prices[0].get('ltp', 0)
            print(f"\n✅✅✅ SUCCESS! BankNifty Price: ₹{price:,.2f}")
        else:
            print(f"❌ No prices in response")
    else:
        print(f"❌ Invalid response")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
