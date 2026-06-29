"""Groww F&O symbol builder — verified format for BankNifty options."""

from datetime import datetime


def groww_option_symbol(index: str, strike: int,
                        opt_type: str, expiry: str) -> str:
    """
    Build Groww FNO trading symbol.
    Verified format: NSE_BANKNIFTY26JUL58200CE
    expiry: '09 Jul 2026'
    """
    dt  = datetime.strptime(expiry, '%d %b %Y')
    mon = dt.strftime('%b').upper()
    yy  = dt.strftime('%y')
    return f"NSE_{index}{yy}{mon}{int(strike)}{opt_type}"
