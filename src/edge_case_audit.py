"""
Edge Case Audit — Every possible failure scenario
As a real trader, these are the things that
cost real money in live trading.
"""

EDGE_CASES = {

    "🔴 CRITICAL — Can cause direct money loss": [
        {
            "case": "Order placed but NOT filled",
            "scenario": "Limit order placed at Rs 265 but premium moved to Rs 280. Order sitting open. Bot thinks trade is active. Next signal fires — bot enters SECOND trade.",
            "current_fix": "NONE ❌",
            "needed": "Fill confirmation check — verify order status before marking as 'in trade'"
        },
        {
            "case": "OCO fails silently",
            "scenario": "Buy order executes. OCO (SL+Target) API call fails silently. Position is OPEN with ZERO protection. If market crashes — unlimited loss.",
            "current_fix": "NONE ❌",
            "needed": "Verify OCO placement. If fails → immediate market exit + Telegram alert"
        },
        {
            "case": "Bot crashes mid-trade",
            "scenario": "Railway.app restarts. Bot loses memory. Trade is open on Groww. Bot comes back and thinks no trade is open. Enters SECOND trade. Now 2 open positions with no monitoring.",
            "current_fix": "Partial — trade_state.json saved ⚠️",
            "needed": "On startup: reconcile bot state with actual Groww positions"
        },
        {
            "case": "Insufficient Groww balance",
            "scenario": "Trade signals fire. Bot calls Groww API. Order rejected silently (not enough margin). Bot marks trade as open. Monitors a position that doesn't exist. SL and target never trigger.",
            "current_fix": "NONE ❌",
            "needed": "Pre-flight balance check before every order"
        },
        {
            "case": "Duplicate entry same day",
            "scenario": "Morning signal fires at 10:15. Bot enters trade. Position closes at 11:30. Afternoon signal fires. Bot enters SECOND trade same day. Fine normally. But if leg1 of first trade didnt close properly — two open positions.",
            "current_fix": "Partial ⚠️",
            "needed": "Hard limit: check Groww positions before any entry"
        },
        {
            "case": "Gap down through SL",
            "scenario": "Overnight news. Market opens 2000 pts below. Your option SL is at Rs 186 but it opens at Rs 40. OCO triggers but fills at Rs 40 not Rs 186. Loss = Rs 225/unit instead of Rs 79/unit.",
            "current_fix": "NONE — this is market reality ⚠️",
            "needed": "Accept this risk. Reduce lot size. Never trade day before big events."
        },
        {
            "case": "Dhan WebSocket disconnects mid-session",
            "scenario": "10:30 AM. BankNifty making big move. WebSocket drops. Bot goes blind. Missing entire move. Worse — if in a trade, trailing SL not updating. Position exposed.",
            "current_fix": "NONE ❌",
            "needed": "Auto-reconnect with exponential backoff. Telegram alert on disconnect."
        },
        {
            "case": "NSE market halt (circuit breaker)",
            "scenario": "Market falls 10%. NSE halts trading. Bot still trying to place orders. All orders get rejected. Bot floods Groww API with failed requests.",
            "current_fix": "NONE ❌",
            "needed": "Circuit breaker detection. Pause all activity if market halted."
        },
    ],

    "🟡 HIGH — Can cause missed profits or extra losses": [
        {
            "case": "Option strike not available",
            "scenario": "Bot calculates strike 58300. Market maker hasn't listed it. Order rejected. Bot doesn't enter trade. Misses the move.",
            "current_fix": "NONE ❌",
            "needed": "Check option chain for available strikes. Use nearest liquid strike."
        },
        {
            "case": "NSE public holiday — bot trades",
            "scenario": "Jan 26, Aug 15, Oct 2. NSE closed. Bot tries to fetch data. Gets errors. Might place erroneous orders.",
            "current_fix": "Partial — event filter has some dates ⚠️",
            "needed": "Complete NSE holiday calendar 2026-2027"
        },
        {
            "case": "Expiry week option behaviour",
            "scenario": "Monday of expiry week. 58300 CE expiring Thursday. Premium decays 50% faster than normal. Bot's target of Rs 530 may never be reached. Exits at breakeven at best.",
            "current_fix": "next_expiry() uses 7+ days ✅ but not perfect",
            "needed": "Always use NEXT week expiry on expiry week Monday"
        },
        {
            "case": "Pre-market gap — stale evening zone",
            "scenario": "Evening zone saved at 57,900-58,100. Next morning BankNifty gaps up to 59,000. Zone is now 1000 pts below price. Bot waits for pullback all day. Never comes. No trade.",
            "current_fix": "Zone stays saved, no trade if no pullback ✅",
            "needed": "If gap > 1.5% from zone — invalidate zone, run fresh analysis"
        },
        {
            "case": "Telegram API fails",
            "scenario": "Entry signal fires. Groww order executes. Telegram fails to send. You have no idea trade is open. Can't monitor manually.",
            "current_fix": "NONE ❌",
            "needed": "Retry Telegram 3 times. Log to file as fallback."
        },
        {
            "case": "Railway.app free tier sleep",
            "scenario": "Railway free tier sleeps after inactivity. Bot goes offline at 11 AM. Misses afternoon trade opportunity. No alert sent.",
            "current_fix": "NONE ❌",
            "needed": "Heartbeat ping to keep Railway awake. Dead man's switch."
        },
        {
            "case": "Groww API rate limiting",
            "scenario": "Bot sends too many requests (position check every 30 sec). Groww throttles. Critical exit order gets delayed. Position exposed.",
            "current_fix": "NONE ❌",
            "needed": "Rate limit handler with queuing. Priority queue for exit orders."
        },
    ],

    "🟢 MEDIUM — Quality improvements": [
        {
            "case": "Partial fill on buy",
            "scenario": "Bot orders 15 units. Only 8 fill (low liquidity). Bot proceeds as if 15 units are open. Leg 1 tries to sell 7 units but only 8 are open. Exit order wrong.",
            "current_fix": "NONE ❌",
            "needed": "Verify actual fill quantity. Adjust all subsequent calculations."
        },
        {
            "case": "Learning engine on bad data",
            "scenario": "First 5 trades all lose (normal learning phase). Bot adjusts min_score to 9+. Now bot barely trades. Misses good 7+ score setups.",
            "current_fix": "Needs minimum 10 trades before threshold change ✅",
            "needed": "Add confidence interval — only change thresholds with statistical significance"
        },
        {
            "case": "Time zone shift",
            "scenario": "India doesn't observe DST. But Railway server (US) does. Cron schedule shifts by 30-60 min. Bot fires at wrong time.",
            "current_fix": "IST timezone used throughout ✅",
            "needed": "Verify Railway server TZ is set to UTC. Always convert to IST."
        },
    ]
}

if __name__ == '__main__':
    total_critical = len(EDGE_CASES["🔴 CRITICAL — Can cause direct money loss"])
    total_high     = len(EDGE_CASES["🟡 HIGH — Can cause missed profits or extra losses"])
    total_medium   = len(EDGE_CASES["🟢 MEDIUM — Quality improvements"])

    print("="*65)
    print("COMPLETE EDGE CASE AUDIT")
    print("="*65)

    for category, cases in EDGE_CASES.items():
        print(f"\n{category}:")
        for i, c in enumerate(cases, 1):
            fix_icon = "✅" if "✅" in c["current_fix"] else ("⚠️" if "⚠️" in c["current_fix"] else "❌")
            print(f"\n  {i}. {c['case']}")
            print(f"     Current: {fix_icon} {c['current_fix']}")
            print(f"     Need:    {c['needed'][:60]}")

    critical_unhandled = sum(1 for c in EDGE_CASES["🔴 CRITICAL — Can cause direct money loss"]
                             if "NONE ❌" in c["current_fix"])
    print(f"\n{'='*65}")
    print(f"SUMMARY:")
    print(f"  🔴 Critical unhandled: {critical_unhandled}/{total_critical}")
    print(f"  🟡 High unhandled:     {sum(1 for c in EDGE_CASES['🟡 HIGH — Can cause missed profits or extra losses'] if '❌' in c['current_fix'])}/{total_high}")
    print(f"  🟢 Medium:             {total_medium}")
    print(f"{'='*65}")
    print(f"\n  NOT READY FOR LIVE TRADING UNTIL CRITICAL ISSUES FIXED")
