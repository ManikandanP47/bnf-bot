"""
VIX percentile — is fear / option premium expensive vs recent history?
"""


def check_vix_rank() -> dict:
    """30-day VIX percentile using yfinance ^INDIAVIX."""
    try:
        import yfinance as yf
        df = yf.Ticker('^INDIAVIX').history(period='3mo', interval='1d')
        if df is None or len(df) < 10:
            return {'available': False}
        closes = df['Close'].dropna()
        current = float(closes.iloc[-1])
        pct = float((closes < current).sum() / len(closes) * 100)
        level = 'LOW' if pct < 35 else 'NORMAL' if pct < 65 else 'HIGH'
        expensive = pct >= 70
        return {
            'available': True,
            'vix': round(current, 2),
            'percentile': round(pct, 0),
            'level': level,
            'expensive': expensive,
            'reason': (
                f"VIX {current:.2f} at {pct:.0f}th percentile ({level}) — "
                + ('premiums rich, caution buyers' if expensive else 'premiums OK')
            ),
            'score': 0 if expensive else (1 if pct < 40 else 0),
        }
    except Exception as e:
        return {'available': False, 'error': str(e)[:40]}
