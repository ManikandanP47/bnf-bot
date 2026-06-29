#!/usr/bin/env python3
"""Test Groww API with introspection to find correct symbol"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("Groww API Introspection")
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
    
    # Check what methods are available
    print(f"\nAPI methods available:")
    methods = [m for m in dir(api) if not m.startswith('_') and callable(getattr(api, m))]
    for m in sorted(methods)[:20]:
        print(f"  - {m}")
    
    # Check SEGMENT constants
    print(f"\nSegment constants:")
    print(f"  SEGMENT_CASH: {api.SEGMENT_CASH}")
    print(f"  SEGMENT_FNO: {api.SEGMENT_FNO}")
    
    # Try searching/listing instruments
    if hasattr(api, 'get_instruments'):
        print(f"\n✓ API has get_instruments method")
        try:
            instruments = api.get_instruments()
            print(f"  Sample instruments: {instruments[:2] if isinstance(instruments, list) else 'Dict'}")
        except Exception as e:
            print(f"  get_instruments error: {str(e)[:60]}")
            
    if hasattr(api, 'search_instruments'):
        print(f"\n✓ API has search_instruments method")
        try:
            # Try searching for banknifty
            result = api.search_instruments('BANKNIFTY')
            print(f"  Search for BANKNIFTY: {result}")
        except Exception as e:
            print(f"  search error: {str(e)[:60]}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
