#!/usr/bin/env python3
"""Final integration test - test the exact flow bot will use"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Import bot modules
sys.path.insert(0, '/Users/manikandan.palanisamy/Downloads/bnf-bot-fixed 2/bnf-bot')

from agents.data_agent import DataAgent
from core.shared_state import STATE

print("\n" + "="*60)
print("FINAL INTEGRATION TEST")
print("="*60)

try:
    print("\n1️⃣ Initializing DataAgent...")
    data_agent = DataAgent()
    
    print("\n2️⃣ Generating TOTP token...")
    token = data_agent.get_groww_token()
    print(f"✅ Token generated: {token[:30]}...{token[-30:]}")
    
    print(f"\n3️⃣ Stored in STATE:")
    stored_token = STATE.get('system.groww_token', '')
    print(f"✅ STATE token: {stored_token[:30]}...{stored_token[-30:]}")
    
    print("\n4️⃣ Fetching BankNifty price...")
    price_data = data_agent.get_live_price()
    
    if price_data:
        print(f"✅✅✅ SUCCESS!")
        print(f"   Price: ₹{price_data['price']:,.2f}")
        print(f"   Source: {price_data['source']}")
    else:
        print(f"❌ No price data returned")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("="*60)
