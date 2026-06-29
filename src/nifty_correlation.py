"""
Nifty ↔ BankNifty intraday correlation.
Don't buy CE when Nifty is weak and BNF is not leading.
"""

import warnings
warnings.filterwarnings('ignore')


def check_nifty_bnf_correlation(bias: str) -> dict:
    """
    Compare today's intraday % change: Nifty 50 vs Bank Nifty.
    Hard block on clear divergence against our bias.
    """
    try:
        import yfinance as yf

        def _day_change(ticker: str) -> float:
            df = yf.Ticker(ticker).history(period='1d', interval='5m')
            if df is None or len(df) < 3:
                return 0.0
            df = df.dropna()
            open_p = float(df['Open'].iloc[0])
            last_p = float(df['Close'].iloc[-1])
            if open_p <= 0:
                return 0.0
            return (last_p - open_p) / open_p * 100

        nifty_chg = _day_change('^NSEI')
        bnf_chg = _day_change('^NSEBANK')

        if bias == 'BULLISH':
            if nifty_chg <= -0.35 and bnf_chg > -0.15:
                return {
                    'ok': False,
                    'reason': (
                        f'📉 Nifty {nifty_chg:+.2f}% but BNF {bnf_chg:+.2f}% — '
                        f'index weak, CE lacks Nifty support'
                    ),
                    'nifty_chg': nifty_chg,
                    'bnf_chg': bnf_chg,
                }
            if nifty_chg >= 0.2 and bnf_chg >= 0.1:
                return {
                    'ok': True,
                    'score_delta': 1,
                    'reason': f'✅ Nifty {nifty_chg:+.2f}% + BNF {bnf_chg:+.2f}% aligned bullish',
                    'nifty_chg': nifty_chg,
                    'bnf_chg': bnf_chg,
                }

        if bias == 'BEARISH':
            if nifty_chg >= 0.35 and bnf_chg < 0.15:
                return {
                    'ok': False,
                    'reason': (
                        f'📈 Nifty {nifty_chg:+.2f}% but BNF {bnf_chg:+.2f}% — '
                        f'index strong, PE lacks Nifty confirmation'
                    ),
                    'nifty_chg': nifty_chg,
                    'bnf_chg': bnf_chg,
                }
            if nifty_chg <= -0.2 and bnf_chg <= -0.1:
                return {
                    'ok': True,
                    'score_delta': 1,
                    'reason': f'✅ Nifty {nifty_chg:+.2f}% + BNF {bnf_chg:+.2f}% aligned bearish',
                    'nifty_chg': nifty_chg,
                    'bnf_chg': bnf_chg,
                }

        return {
            'ok': True,
            'score_delta': 0,
            'reason': f'Nifty {nifty_chg:+.2f}% | BNF {bnf_chg:+.2f}% — neutral correlation',
            'nifty_chg': nifty_chg,
            'bnf_chg': bnf_chg,
        }
    except Exception as e:
        return {
            'ok': True,
            'score_delta': 0,
            'reason': f'Correlation check skipped ({str(e)[:30]})',
        }
