#!/usr/bin/env python3
"""Check get_ltp method signature"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp
import inspect

print("\n" + "="*60)
print("get_ltp Method Signature")
print("="*60)

try:
    # Generate fresh token
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    
    token = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
    api = GrowwAPI(token)
    
    # Get method signature
    sig = inspect.signature(api.get_ltp)
    print(f"Signature: get_ltp{sig}")
    
    # Get docstring
    print(f"\nDocstring:\n{api.get_ltp.__doc__}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
