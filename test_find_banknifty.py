#!/usr/bin/env python3
"""Find correct BankNifty symbol in Groww API"""

import os
from dotenv import load_dotenv
load_dotenv()

from growwapi import GrowwAPI
import pyotp

print("\n" + "="*60)
print("Finding BankNifty Symbol")
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
    
    # Try method 1: Get instrument by exchange and trading symbol
    print(f"\nMethod 1: get_instrument_by_exchange_and_trading_symbol")
    symbols_to_try = ['BANKNIFTY', 'NIFTY BANK', 'NIFTYBANK']
    for sym in symbols_to_try:
        try:
            result = api.get_instrument_by_exchange_and_trading_symbol(
                exchange='NSE',
                trading_symbol=sym
            )
            print(f"  ✓ {sym}: {result}")
        except Exception as e:
            print(f"  ✗ {sym}: {str(e)[:50]}")
    
    # Try method 2: Search in all instruments
    print(f"\nMethod 2: Searching in all instruments...")
    try:
        all_instruments = api.get_all_instruments()
        print(f"  Total instruments: {len(all_instruments)}")
        
        # Search for BANK related instruments
        bank_instr = [i for i in all_instruments if 'BANK' in str(i).upper()][:5]
        print(f"  BANK instruments found: {len(bank_instr)}")
        for instr in bank_instr[:3]:
            print(f"    - {instr}")
            
    except Exception as e:
        print(f"  Error: {str(e)[:60]}")
    
    # Method 3: Get all FNO instruments
    print(f"\nMethod 3: Getting FNO contracts...")
    try:
        contracts = api.get_contracts(segment=api.SEGMENT_FNO)
        print(f"  Total FNO contracts: {len(contracts)}")
        
        # Find BANK 
        bank_contracts = [c for c in contracts if 'BANK' in str(c).upper()][:3]
        print(f"  BANK contracts:")
        for contract in bank_contracts:
            print(f"    - {contract}")
            
    except Exception as e:
        print(f"  Error: {str(e)[:60]}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
