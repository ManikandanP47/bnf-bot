#!/usr/bin/env python3
"""Test different symbol formats for Groww API"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("Testing symbol formats with Groww API")
print("="*60)

try:
    # Generate fresh token
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    
    token = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
    print(f"✅ Token generated")
    
    api = GrowwAPI(token)
    
    # Try different formats
    symbols_to_try = [
        'NIFTY BANK',
        'BANKNIFTY',
        'NSE_BANKNIFTY',
        'BANKNIFTY1!',
        'NIFTYBANK',
    ]
    
    for symbol in symbols_to_try:
        print(f"\nTrying: {symbol}")
        try:
            q = api.get_ltp(
                exchange_trading_symbols=(symbol,),
                segment=api.SEGMENT_FNO
            )
            if q and q.get('ltps'):
                price = q['ltps'][0].get('ltp', 0)
                print(f"  ✅ SUCCESS: Price = {price}")
                break
        except Exception as e:
            print(f"  ❌ {str(e)[:60]}")
            
except Exception as e:
    print(f"Error: {e}")

print("="*60)
