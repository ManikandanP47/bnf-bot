#!/usr/bin/env python3
"""Test direct Groww API with ACCESS_TOKEN"""

import os
from dotenv import load_dotenv
load_dotenv()

print("\n" + "="*60)
print("TEST: Direct Groww API with ACCESS_TOKEN")
print("="*60)

try:
    from growwapi import GrowwAPI
    
    token = os.getenv('GROWW_ACCESS_TOKEN', '')
    print(f"Token loaded: {token[:30]}...{token[-30:]}")
    
    print(f"\nCreating GrowwAPI instance...")
    api = GrowwAPI(token)
    
    # Try FNO segment (BankNifty is derivatives)
    print(f"Fetching BNF price (trying FNO segment)...")
    q = api.get_ltp(
        exchange_trading_symbols=('NIFTY BANK',),
        segment=api.SEGMENT_FNO
    )
    
    print(f"Response: {q}")
    
    if q and isinstance(q, dict):
        prices = q.get('ltps', [])
        if prices:
            price = prices[0].get('ltp', 0) or prices[0].get('last_price', 0)
            print(f"\n✅ SUCCESS: BNF Price: ₹{price:,.2f}")
        else:
            print(f"❌ No prices in response")
    else:
        print(f"❌ Invalid response: {q}")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
