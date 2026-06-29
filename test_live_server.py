#!/usr/bin/env python3
"""
Quick live-server smoke test (DigitalOcean / production).
Uses the same Groww symbol as the running bot: NSE_BANKNIFTY

Run: ./venv/bin/python test_live_server.py
"""

import os
import socket
import sys
from dotenv import load_dotenv

load_dotenv()

PASS = 0
FAIL = 0
LINES = []

# Same symbol as agents/data_agent.py — NOT "NIFTY BANK" or "NSE:NIFTY BANK"
BANKNIFTY_SYMBOL = "NSE_BANKNIFTY"


def record(ok: bool, label: str, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        mark = "✅"
    else:
        FAIL += 1
        mark = "❌"
    line = f"{mark} {label}" + (f": {detail}" if detail else "")
    LINES.append(line)
    print(f"   {mark} {label}" + (f": {detail}" if detail else ""))


def main():
    print("\n1. TOTP...")
    try:
        import pyotp
        secret = os.getenv("GROWW_TOTP_SECRET", "")
        if not secret:
            raise ValueError("GROWW_TOTP_SECRET missing")
        code = pyotp.TOTP(secret).now()
        record(True, "TOTP", code)
    except Exception as e:
        record(False, "TOTP", str(e)[:80])
        code = None

    print("\n2. Groww Auth...")
    token = None
    try:
        from growwapi import GrowwAPI
        api_key = os.getenv("GROWW_TOTP_TOKEN", "")
        if not code or not api_key:
            raise ValueError("missing TOTP or GROWW_TOTP_TOKEN")
        token = GrowwAPI.get_access_token(api_key=api_key, totp=code)
        if not token:
            raise ValueError("empty access token")
        record(True, "Auth", f"Token: {token[:28]}...")
    except Exception as e:
        record(False, "Auth", str(e)[:80])

    print("\n3. BankNifty live price...")
    price = 0
    if token:
        try:
            from src.groww_client import get_groww_client
            groww = get_groww_client(token)
            q = groww.get_ltp(
                exchange_trading_symbols=(BANKNIFTY_SYMBOL,),
                segment=groww.SEGMENT_CASH,
            )
            if isinstance(q, dict):
                if BANKNIFTY_SYMBOL in q:
                    price = float(q[BANKNIFTY_SYMBOL])
                elif q.get("ltps"):
                    price = float(q["ltps"][0].get("ltp", 0) or 0)
            if price > 0:
                record(True, "BankNifty price", f"₹{price:,.2f} ({BANKNIFTY_SYMBOL})")
            else:
                record(False, "BankNifty price", f"empty response: {str(q)[:80]}")
        except Exception as e:
            record(False, "BankNifty price", str(e)[:80])
    else:
        record(False, "BankNifty price", "no token")

    print("\n4. F&O margin...")
    if token:
        try:
            from src.groww_client import get_groww_client
            groww = get_groww_client(token)
            margin = groww.get_available_margin_details()
            record(True, "Margin", str(margin)[:60])
        except Exception as e:
            record(False, "Margin", str(e)[:80])
    else:
        record(False, "Margin", "no token")

    print("\n5. Current positions...")
    if token:
        try:
            from src.groww_client import get_groww_client
            groww = get_groww_client(token)
            pos = groww.get_positions_for_user(segment=groww.SEGMENT_FNO)
            n = len((pos or {}).get("positions", []))
            record(True, "Positions", f"{n} open")
        except Exception as e:
            record(False, "Positions", str(e)[:80])
    else:
        record(False, "Positions", "no token")

    print("\n6. Paper trade simulation...")
    try:
        from src.trade_filters import get_dynamic_sl_target
        dyn = get_dynamic_sl_target(265)
        record(True, "Paper trade", f"SL: Rs{dyn['sl_prem']} | Target: Rs{dyn['tgt_prem']}")
    except Exception as e:
        record(False, "Paper trade", str(e)[:80])

    total = PASS + FAIL
    host = socket.gethostname()
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "unknown"

    summary = (
        f"🧪 *Live Server Test Complete*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Server: {ip} ({host})\n"
        f"Result: {PASS}/{total} passed\n\n"
        + "\n".join(LINES)
    )
    if FAIL:
        summary += "\n\n⚠️ Some issues found"
    else:
        summary += "\n\n🎉 All checks passed — bot symbol OK"

    print("\n" + "=" * 50)
    print(f"RESULT: {PASS}/{total} passed")
    print(summary.replace("*", ""))

    try:
        from core.messenger import Messenger
        Messenger().send(summary)
    except Exception:
        pass

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
