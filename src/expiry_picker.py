"""
Bank Nifty monthly expiry — last Tuesday of month (NSE).
Weekly BNF options discontinued Nov 2024; monthly only.
Pick expiries with enough days left — avoids expiry-week theta crush on ₹5k accounts.
"""

from datetime import datetime, timedelta
import calendar
import pytz

IST = pytz.timezone('Asia/Kolkata')
BNF_EXPIRY_WEEKDAY = 1  # Tuesday (monthly)


def banknifty_monthly_expiry(for_date=None):
    """Last Tuesday of the month."""
    d = for_date or datetime.now(IST).date()
    last_day = calendar.monthrange(d.year, d.month)[1]
    cur = d.replace(day=last_day)
    while cur.weekday() != BNF_EXPIRY_WEEKDAY:
        cur = cur.replace(day=cur.day - 1)
    return cur


def next_banknifty_expiry(min_days_ahead: int = 5) -> str:
    """
    Next monthly expiry (last Tuesday) at least `min_days_ahead` calendar days away.
    """
    today = datetime.now(IST).date()
    for month_shift in range(0, 4):
        y = today.year + (today.month + month_shift - 1) // 12
        m = (today.month + month_shift - 1) % 12 + 1
        exp = banknifty_monthly_expiry(today.replace(year=y, month=m, day=1))
        if (exp - today).days >= min_days_ahead:
            return exp.strftime('%d %b %Y')
    fallback = today + timedelta(days=28)
    return banknifty_monthly_expiry(fallback).strftime('%d %b %Y')


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
    """Within 4 calendar days of monthly expiry (last Tuesday)."""
    today = datetime.now(IST).date()
    exp = banknifty_monthly_expiry(today)
    return 0 <= (exp - today).days <= 4


def is_expiry_day() -> bool:
    today = datetime.now(IST).date()
    return today == banknifty_monthly_expiry(today)
