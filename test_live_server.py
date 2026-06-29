"""
Live API Test — Run this on 64.227.177.10
Tests Groww from the whitelisted IP
Sends results to Telegram
"""

import os, sys, time, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import requests

TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT    = os.getenv('TELEGRAM_CHAT_ID')

def tg(msg):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TOKEN}/sendMessage',
            json={'chat_id': CHAT, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=10
        )
    except: pass

results = []
tg("🧪 *Live Server Test Started*\nTesting from 64.227.177.10...")

# Test 1: TOTP
print("\n1. TOTP...")
try:
    import pyotp
    secret = os.getenv('GROWW_TOTP_SECRET','')
    totp   = pyotp.TOTP(secret)
    code   = totp.now()
    results.append(f"✅ TOTP: {code}")
    print(f"   ✅ {code}")
except Exception as e:
    results.append(f"❌ TOTP: {e}")

# Test 2: Groww Auth
print("\n2. Groww Auth...")
LIVE_TOKEN = None
try:
    from growwapi import GrowwAPI
    import pyotp
    secret     = os.getenv('GROWW_TOTP_SECRET','')
    totp_token = os.getenv('GROWW_TOTP_TOKEN','')
    code       = pyotp.TOTP(secret).now()
    LIVE_TOKEN = GrowwAPI.get_access_token(api_key=totp_token, totp=code)
    results.append(f"✅ Auth: token obtained")
    print(f"   ✅ Token: {str(LIVE_TOKEN)[:30]}...")
except Exception as e:
    results.append(f"❌ Auth: {e}")
    print(f"   ❌ {e}")
    LIVE_TOKEN = os.getenv('GROWW_ACCESS_TOKEN','')

# Test 3: BankNifty Live Price
print("\n3. BankNifty live price...")
try:
    from growwapi import GrowwAPI
    groww = GrowwAPI(LIVE_TOKEN)
    ltp   = groww.get_ltp(
        exchange_trading_symbols=("NSE:NIFTY BANK",),
        segment="CASH"
    )
    price = ltp.get('NSE:NIFTY BANK',{}).get('ltp', 0) or str(ltp)[:60]
    results.append(f"✅ BankNifty: ₹{price}")
    print(f"   ✅ Price: {price}")
except Exception as e:
    results.append(f"❌ BankNifty price: {e}")
    print(f"   ❌ {e}")

# Test 4: F&O Margin
print("\n4. F&O margin...")
try:
    from growwapi import GrowwAPI
    groww  = GrowwAPI(LIVE_TOKEN)
    margin = groww.get_available_margin_details()
    results.append(f"✅ Margin: {str(margin)[:50]}")
    print(f"   ✅ Margin: {str(margin)[:60]}")
except Exception as e:
    results.append(f"❌ Margin: {e}")
    print(f"   ❌ {e}")

# Test 5: Positions
print("\n5. Current positions...")
try:
    from growwapi import GrowwAPI
    groww = GrowwAPI(LIVE_TOKEN)
    pos   = groww.get_positions_for_user(segment="FNO")
    count = len(pos.get('positions',[]) if isinstance(pos,dict) else [])
    results.append(f"✅ Positions: {count} open")
    print(f"   ✅ {count} open positions")
except Exception as e:
    results.append(f"❌ Positions: {e}")
    print(f"   ❌ {e}")

# Test 6: Paper trade simulation
print("\n6. Paper trade simulation...")
try:
    from src.trade_filters import get_dynamic_sl_target, get_partial_exit_levels
    dyn = get_dynamic_sl_target(265)
    pe  = get_partial_exit_levels(265)
    results.append(f"✅ Paper trade: SL Rs{dyn['sl_prem']} Tgt Rs{dyn['tgt_prem']}")
    print(f"   ✅ SL: Rs{dyn['sl_prem']} | Target: Rs{dyn['tgt_prem']}")
except Exception as e:
    results.append(f"❌ Paper trade: {e}")

# Send full results to Telegram
passed = sum(1 for r in results if r.startswith('✅'))
report = (
    f"🧪 *Live Server Test Complete*\n"
    f"━━━━━━━━━━━━━━━━━━━━━\n"
    f"Server: 64.227.177.10\n"
    f"Result: {passed}/{len(results)} passed\n\n"
)
for r in results:
    report += f"{r}\n"

report += f"\n{'✅ BOT READY FOR TRADING' if passed==len(results) else '⚠️ Some issues found'}"
tg(report)
print(f"\n{'='*50}")
print(f"RESULT: {passed}/{len(results)} passed")
print(report)
