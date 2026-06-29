#!/usr/bin/env python3
"""Test with Groww symbol format"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("Testing Groww Symbol Format")
print("="*60)

try:
    # Generate fresh token
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    
    token = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
    print(f"✅ Token obtained")
    
    api = GrowwAPI(token)
    
    # Try different symbol formats
    test_symbols = [
        ('NSE-BANKNIFTY', api.SEGMENT_CASH),
        ('NSE:BANKNIFTY', api.SEGMENT_CASH),
        ('NSE_BANKNIFTY', api.SEGMENT_CASH),
    ]
    
    for symbol, segment in test_symbols:
        print(f"\nTrying: {symbol} (segment={segment})")
        try:
            q = api.get_ltp(
                exchange_trading_symbols=(symbol,),
                segment=segment
            )
            if q and q.get('ltps'):
                price = q['ltps'][0].get('ltp', 0)
                print(f"  ✅ SUCCESS: Price = ₹{price:,.2f}")
                break
        except Exception as e:
            print(f"  ❌ {str(e)[:50]}")

except Exception as e:
    print(f"Error: {e}")

print("="*60)
