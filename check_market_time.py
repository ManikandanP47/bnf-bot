#!/usr/bin/env python3
"""Check current market time and status"""

from datetime import datetime, time as dtime
import pytz

IST = pytz.timezone('Asia/Kolkata')
now = datetime.now(IST)

print(f"\n🕐 Current IST Time: {now.strftime('%A, %d %b %Y %H:%M:%S')}")
print(f"   Weekday: {now.strftime('%A')} (0=Mon, 4=Fri, 5=Sat, 6=Sun)")

# Market hours check
market_open = dtime(9, 0)
market_close = dtime(15, 50)
current_time = now.time()
is_market_time = market_open <= current_time <= market_close

print(f"\n📊 Market Status:")
print(f"   Market Hours: 9:00 AM - 3:50 PM IST")
print(f"   Current Time: {current_time.strftime('%H:%M:%S')}")
print(f"   Market Open: {is_market_time}")

weekday = now.weekday()
print(f"\n📅 Trading Day Check:")
print(f"   Weekday (0-6): {weekday}")
if weekday < 5:
    print(f"   ✅ Weekday (trading day)")
elif weekday == 5:
    print(f"   ❌ Saturday (no trading)")
else:
    print(f"   ❌ Sunday (no trading)")

if is_market_time and weekday < 5:
    print(f"\n✅ MARKET IS LIVE - Bot should be generating signals!")
else:
    print(f"\n❌ Market is closed")
