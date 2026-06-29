"""
Open Interest (OI) Analysis — The Professional Edge
Every serious BankNifty F&O trader checks this FIRST.

What OI tells us:
  Highest CALL OI strike = where institutions sell calls = RESISTANCE
  Highest PUT OI  strike = where institutions sell puts  = SUPPORT
  Max Pain        strike = price gravitates here at expiry
  PCR (Put/Call ratio)   = overall market sentiment

Why this matters for our trades:
  If we want to buy CE target 59,000
  But highest CALL OI is at 58,500
  = Institutions have SOLD 58,500 CE aggressively
  = They will DEFEND that level
  = Our target may never be reached
  = Don't enter OR lower the target ✅
"""

import requests
import json
import os
from datetime import datetime
import pytz
import warnings
warnings.filterwarnings('ignore')

IST = pytz.timezone('Asia/Kolkata')

# NSE headers required to bypass bot detection
NSE_HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer':         'https://www.nseindia.com/option-chain',
    'Accept':          '*/*',
}


def get_oi_data(index: str = 'BANKNIFTY',
                timeout: int = 10) -> dict:
    """
    Fetch BankNifty option chain from NSE.
    Returns OI data for all strikes.
    Uses session cookies to bypass NSE bot detection.
    """
    try:
        session = requests.Session()
        session.headers.update(NSE_HEADERS)

        # First hit the main page to get session cookies
        session.get('https://www.nseindia.com',
                    timeout=timeout, verify=False)

        # Now fetch option chain
        url  = f'https://www.nseindia.com/api/option-chain-indices?symbol={index}'
        resp = session.get(url, timeout=timeout, verify=False)

        if resp.status_code == 200:
            return resp.json()
        return None

    except Exception as e:
        return None


def calculate_max_pain(oi_data: dict) -> dict:
    """
    Max Pain = Strike price where total OI losses are maximum
    i.e. where maximum number of options expire WORTHLESS

    Formula:
    For each strike, calculate total pain for CALL and PUT holders
    Sum them → find the strike with minimum total pain for OI writers
    = maximum pain for option BUYERS = Max Pain

    Why traders watch this:
    Market makers (who sold all those options) have INCENTIVE
    to pin price near Max Pain at expiry.
    """
    try:
        if not oi_data:
            return {'available': False}

        records = oi_data.get('records', {})
        data    = records.get('data', [])

        if not data:
            return {'available': False}

        # Build strike price map
        strikes = {}
        for record in data:
            sp = record.get('strikePrice', 0)
            ce = record.get('CE', {})
            pe = record.get('PE', {})

            if sp:
                strikes[sp] = {
                    'ce_oi': ce.get('openInterest', 0),
                    'pe_oi': pe.get('openInterest', 0)
                }

        if not strikes:
            return {'available': False}

        sorted_strikes = sorted(strikes.keys())

        # Max Pain calculation
        pain = {}
        for target in sorted_strikes:
            total_pain = 0
            for strike, oi in strikes.items():
                # Pain for CE holders (calls become worthless below target)
                if target < strike:
                    total_pain += oi['ce_oi'] * (strike - target)
                # Pain for PE holders (puts become worthless above target)
                if target > strike:
                    total_pain += oi['pe_oi'] * (target - strike)
            pain[target] = total_pain

        max_pain_strike = min(pain, key=pain.get)

        # Total OI stats
        total_ce = sum(v['ce_oi'] for v in strikes.values())
        total_pe = sum(v['pe_oi'] for v in strikes.values())
        pcr      = round(total_pe / total_ce, 2) if total_ce > 0 else 1.0

        # Find highest OI strikes
        max_ce_strike = max(strikes, key=lambda s: strikes[s]['ce_oi'])
        max_pe_strike = max(strikes, key=lambda s: strikes[s]['pe_oi'])

        return {
            'available':      True,
            'max_pain':       max_pain_strike,
            'resistance':     max_ce_strike,   # Highest CALL OI = resistance
            'support':        max_pe_strike,   # Highest PUT OI  = support
            'pcr':            pcr,
            'total_ce_oi':    total_ce,
            'total_pe_oi':    total_pe,
            'pcr_signal':     'BULLISH' if pcr > 1.2 else ('BEARISH' if pcr < 0.7 else 'NEUTRAL'),
            'strikes':        strikes,
            'timestamp':      datetime.now(IST).strftime('%I:%M %p')
        }

    except Exception as e:
        return {'available': False, 'error': str(e)}


def analyse_oi_for_trade(current_price: float,
                          our_target: float,
                          bias: str) -> dict:
    """
    Main function: Should we enter this trade based on OI?

    Checks:
    1. Is our target blocked by heavy CALL OI resistance?
    2. Is price far from Max Pain (will it be pinned)?
    3. Does PCR support our direction?
    4. Is there OI support below for CE trade?
    """
    raw = get_oi_data()
    if not raw:
        return {
            'available':  False,
            'proceed':    True,  # Proceed if OI data unavailable
            'reason':     'OI data unavailable — proceeding without OI filter'
        }

    oi = calculate_max_pain(raw)
    if not oi.get('available'):
        return {
            'available': False,
            'proceed':   True,
            'reason':    'OI calculation failed — proceeding'
        }

    issues   = []
    support  = []
    proceed  = True

    resistance = oi['resistance']
    support_lv = oi['support']
    max_pain   = oi['max_pain']
    pcr        = oi['pcr']
    pcr_signal = oi['pcr_signal']

    # ── Check 1: Is target blocked by resistance? ─────────────────
    if bias == 'BULLISH':
        if our_target > resistance:
            # Our target is ABOVE the resistance
            target_gap  = our_target - resistance
            target_gap_pct = target_gap / current_price * 100
            if target_gap_pct > 0.5:  # More than 0.5% above resistance
                issues.append(
                    f"⚠️ Target {our_target:,.0f} is above heavy CE OI at {resistance:,.0f}"
                    f" — lower target to {resistance - 100:,.0f}"
                )
                our_target = resistance - 100  # Adjust target below resistance

    # ── Check 2: PCR alignment ────────────────────────────────────
    if bias == 'BULLISH' and pcr_signal == 'BEARISH':
        issues.append(
            f"⚠️ PCR {pcr:.2f} is bearish (< 0.7) — smart money is bearish"
        )
    elif bias == 'BULLISH' and pcr_signal == 'BULLISH':
        support.append(
            f"✅ PCR {pcr:.2f} bullish (> 1.2) — supports CE trade"
        )

    if bias == 'BEARISH' and pcr_signal == 'BULLISH':
        issues.append(
            f"⚠️ PCR {pcr:.2f} is bullish — market expects upside"
        )

    # ── Check 3: Max Pain proximity ───────────────────────────────
    distance_to_maxpain     = abs(current_price - max_pain)
    distance_to_maxpain_pct = distance_to_maxpain / current_price * 100

    if distance_to_maxpain_pct < 0.5:
        support.append(
            f"✅ Price near Max Pain {max_pain:,.0f} — stable zone"
        )
    elif distance_to_maxpain_pct > 2.0:
        support.append(
            f"📌 Max Pain {max_pain:,.0f} | Price {current_price:,.0f} "
            f"({distance_to_maxpain_pct:.1f}% away) — watch this level"
        )

    # ── Check 4: OI support for CE trade ─────────────────────────
    if bias == 'BULLISH' and support_lv > 0:
        if support_lv <= current_price:
            support.append(
                f"✅ Strong PUT OI support at {support_lv:,.0f} "
                f"— floor below price, protects trade"
            )

    # Final decision
    # Only block trade if both PCR and resistance say NO
    critical_issues = [i for i in issues if '⚠️' in i]
    if len(critical_issues) >= 2:
        proceed = False

    return {
        'available':       True,
        'proceed':         proceed,
        'max_pain':        max_pain,
        'resistance':      resistance,
        'support':         support_lv,
        'pcr':             pcr,
        'pcr_signal':      pcr_signal,
        'adjusted_target': our_target,
        'issues':          issues,
        'supporting':      support,
        'summary': (
            f"OI: PCR {pcr:.2f} ({pcr_signal}) | "
            f"Resistance {resistance:,.0f} | "
            f"Support {support_lv:,.0f} | "
            f"Max Pain {max_pain:,.0f}"
        )
    }


def oi_telegram_message(oi_result: dict) -> str:
    """Format OI analysis for Telegram"""
    if not oi_result.get('available'):
        return ""

    lines = [
        f"\n📊 *OI Analysis:*",
        f"  PCR: {oi_result['pcr']:.2f} ({oi_result['pcr_signal']})",
        f"  Resistance: {oi_result['resistance']:,} (heavy CE OI)",
        f"  Support:    {oi_result['support']:,} (heavy PE OI)",
        f"  Max Pain:   {oi_result['max_pain']:,}",
    ]

    for s in oi_result.get('supporting', []):
        lines.append(f"  {s}")

    for i in oi_result.get('issues', []):
        lines.append(f"  {i}")

    return '\n'.join(lines)


if __name__ == '__main__':
    print("OI Analysis Test...")
    result = analyse_oi_for_trade(
        current_price = 58187,
        our_target    = 59000,
        bias          = 'BULLISH'
    )
    if result.get('available'):
        print(f"Proceed: {result['proceed']}")
        print(f"Summary: {result['summary']}")
        for s in result.get('supporting', []):
            print(f"  {s}")
        for i in result.get('issues', []):
            print(f"  {i}")
    else:
        print(f"OI not available: {result.get('reason')}")
