#!/usr/bin/env python3
"""Check current bot state and signal pipeline"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, '/Users/manikandan.palanisamy/Downloads/bnf-bot-fixed 2/bnf-bot')

from core.shared_state import STATE
from agents.data_agent import DataAgent
from agents.analysis_agent import AnalysisAgent

print("\n" + "="*70)
print("📊 LIVE BOT STATE CHECK")
print("="*70)

# Check data collection
print("\n[1] DATA COLLECTION")
data = DataAgent()
price = data.get_live_price()
print(f"  ✅ Current BNF Price: ₹{price.get('price', 0):,.2f}")

# Check candle formation
print(f"\n[2] CANDLE FORMATION")
print(f"  1-min candles: {len(data.b1.get_candles())}")
print(f"  5-min candles: {len(data.b5.get_candles())}")
print(f"  15-min candles: {len(data.b15.get_candles())}")

# Show latest candles
if data.b1.get_candles():
    latest_1m = data.b1.get_candles()[-1]
    print(f"\n  📊 Latest 1-min candle:")
    print(f"     Open:  ₹{latest_1m['open']:,.2f}")
    print(f"     High:  ₹{latest_1m['high']:,.2f}")
    print(f"     Low:   ₹{latest_1m['low']:,.2f}")
    print(f"     Close: ₹{latest_1m['close']:,.2f}")

# Check signal state
print(f"\n[3] SIGNAL STATE")
signal = STATE.get('signal', {})
if signal:
    print(f"  ✅ Active signal: {signal}")
else:
    print(f"  ⏳ No signal yet (waiting for pattern formation)")

# Check errors
print(f"\n[4] SYSTEM STATE")
errors = STATE.get('errors', [])
if errors:
    print(f"  ⚠️  Errors: {errors[-3:]}")
else:
    print(f"  ✅ No errors")

print("="*70 + "\n")
