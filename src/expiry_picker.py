"""
BankNifty weekly expiry = Wednesday (NSE).
Pick expiries with enough days left — avoids expiry-week theta crush on ₹5k accounts.
"""

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')
BNF_EXPIRY_WEEKDAY = 2  # Wednesday


def next_banknifty_expiry(min_days_ahead: int = 5) -> str:
    """
    Next Wednesday expiry at least `min_days_ahead` calendar days away.
    On expiry week Mon/Tue, skips this Wednesday → next week's expiry.
    """
    today = datetime.now(IST).date()
    for d in range(1, 45):
        c = today + timedelta(days=d)
        if c.weekday() != BNF_EXPIRY_WEEKDAY:
            continue
        if (c - today).days >= min_days_ahead:
            return c.strftime('%d %b %Y')
    fallback = today + timedelta(days=14)
    return fallback.strftime('%d %b %Y')


def days_to_expiry(expiry_str: str) -> int:
    """Calendar days from today to expiry ('09 Jul 2026')."""
    if not expiry_str:
        return 0
    try:
        exp = datetime.strptime(expiry_str, '%d %b %Y').date()
        return (exp - datetime.now(IST).date()).days
    except ValueError:
        return 0


def is_expiry_week() -> bool:
    """Within 4 calendar days of a Wednesday expiry."""
    today = datetime.now(IST).date()
    for d in range(0, 5):
        c = today + timedelta(days=d)
        if c.weekday() == BNF_EXPIRY_WEEKDAY:
            return True
    return False
