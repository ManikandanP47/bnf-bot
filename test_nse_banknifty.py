#!/usr/bin/env python3
"""Test with NSE_BANKNIFTY format"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("Testing NSE_BANKNIFTY Format")
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
    
    # Use the documented format
    print(f"\nFetching price with format: NSE_BANKNIFTY")
    q = api.get_ltp(
        exchange_trading_symbols=('NSE_BANKNIFTY',),
        segment=api.SEGMENT_CASH
    )
    
    print(f"Response: {q}")
    
    if q and isinstance(q, dict):
        prices = q.get('ltps', [])
        print(f"Prices in response: {len(prices)}")
        if prices:
            price = prices[0].get('ltp', 0)
            print(f"\n✅✅✅ SUCCESS! BankNifty Price: ₹{price:,.2f}")
            print(f"Full price data: {prices[0]}")
        else:
            print(f"❌ No prices in response")
    else:
        print(f"❌ Invalid response")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
