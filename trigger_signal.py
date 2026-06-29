#!/usr/bin/env python3
"""Manually trigger a test signal to Telegram"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, '/Users/manikandan.palanisamy/Downloads/bnf-bot-fixed 2/bnf-bot')

from core.messenger import Messenger

print("\n" + "="*60)
print("📡 TRIGGERING TEST SIGNAL TO TELEGRAM")
print("="*60)

messenger = Messenger()

# Create a realistic BUY signal
signal = {
    'type': 'BUY',
    'price': 58232.50,
    'quantity': 15,
    'target': 58450.00,
    'stop_loss': 58000.00,
    'timeframe': '5M',
    'pattern': 'Breaker Block + SMC',
    'confidence': 92,
    'brain_approval': True
}

# Format signal
message = f"""
🟢 *BUY SIGNAL TRIGGERED* 🟢

*Symbol:* BankNifty
*Entry:* ₹{signal['price']:,.2f}
*Target:* ₹{signal['target']:,.2f}
*Stop Loss:* ₹{signal['stop_loss']:,.2f}
*Quantity:* {signal['quantity']} lots

*Setup:* {signal['pattern']}
*Timeframe:* {signal['timeframe']}
*Confidence:* {signal['confidence']}%
✅ Brain Approved

*Mode:* 📝 PAPER (Test)
"""

print("\n📨 Sending to Telegram...")
try:
    success = messenger.send(message.strip())
    if success:
        print("✅ Signal delivered to Telegram!")
        print(f"\nMessage content:")
        print(message)
    else:
        print("❌ Failed to send")
except Exception as e:
    print(f"❌ Error: {e}")

print("="*60)
