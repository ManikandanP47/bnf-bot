#!/usr/bin/env python3
"""
Quick Telegram Test — Send BNF price immediately from Groww API
"""

import os
import sys
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from core.messenger import Messenger

IST = pytz.timezone('Asia/Kolkata')

def get_bnf_price_from_groww():
    """Get current BNF price from Groww API"""
    try:
        from growwapi import GrowwAPI
        token = os.getenv('GROWW_ACCESS_TOKEN', '')
        
        if not token:
            print("⚠️  No GROWW_ACCESS_TOKEN, trying TOTP...")
            import pyotp
            secret = os.getenv('GROWW_TOTP_SECRET', '')
            totp_token = os.getenv('GROWW_TOTP_TOKEN', '')
            if secret and totp_token:
                totp = pyotp.TOTP(secret).now()
                token = GrowwAPI.get_access_token(api_key=totp_token, totp=totp)
        
        if token:
            api = GrowwAPI(token)
            q = api.get_ltp(
                exchange_trading_symbols=('NIFTY BANK',),
                segment=api.SEGMENT_CASH
            )
            if q and isinstance(q, dict):
                prices = q.get('ltps', [])
                if prices:
                    price = float(prices[0].get('ltp', 0) or prices[0].get('last_price', 0))
                    if price > 0:
                        return price
    except Exception as e:
        print(f"⚠️  Groww API failed: {e}")
    return 0

def test_telegram():
    """Send test Telegram message with BNF price from Groww"""
    messenger = Messenger()
    
    price = get_bnf_price_from_groww()
    now = datetime.now(IST).strftime('%H:%M:%S')
    
    if price > 0:
        msg = f"""🧪 *TELEGRAM TEST - GROWW API*
━━━━━━━━━━━━━━━━━━━━━━
⏰ Time: {now}
📊 BNF Price: ₹{price:,.0f}
🔌 Source: Groww API ✅
🤖 Bot: Operational ✅
        
Status: Connection working
"""
    else:
        msg = f"""🧪 *TELEGRAM TEST - GROWW API*
━━━━━━━━━━━━━━━━━━━━━━
⏰ Time: {now}
📡 Data: Groww API (couldn't connect)
🤖 Bot: Ready to trade

Status: Connection working
"""
    
    print(f"📤 Sending test message...")
    print(f"Message:\n{msg}")
    
    success = messenger.send(msg)
    
    if success:
        print("✅ Telegram sent successfully!")
        return True
    else:
        print("❌ Telegram failed - check:")
        print(f"  - TELEGRAM_BOT_TOKEN set: {bool(os.getenv('TELEGRAM_BOT_TOKEN'))}")
        print(f"  - TELEGRAM_CHAT_ID set: {bool(os.getenv('TELEGRAM_CHAT_ID'))}")
        return False

if __name__ == '__main__':
    test_telegram()
