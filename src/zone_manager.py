"""
Zone Manager — The Hybrid Link
Evening bot SAVES the zone.
Morning bot READS and WAITS for price to reach it.
"""

import json
import os
from datetime import datetime
import pytz

IST        = pytz.timezone('Asia/Kolkata')
ZONE_FILE  = 'daily_zone.json'


def save_zone(analysis: dict):
    """
    Called by evening bot (7:30 PM) after daily scan.
    Saves the key OB zone for tomorrow's intraday entry.
    """
    if not analysis.get('setup'):
        # No setup found — clear any old zone
        clear_zone()
        return

    zone = {
        'date':       datetime.now(IST).strftime('%d %b %Y'),
        'bias':       analysis.get('trend'),
        'zone_low':   None,
        'zone_high':  None,
        'score':      analysis.get('score', 0),
        'name':       analysis.get('name'),
        'strike':     analysis.get('strike'),
        'opt_type':   analysis.get('opt_type'),
        'expiry':     analysis.get('expiry'),
        'premium':    analysis.get('premium'),
        'sl_prem':    analysis.get('sl_prem'),
        'tgt_prem':   analysis.get('tgt_prem'),
        'lot_cost':   analysis.get('lot_cost'),
        'max_loss':   analysis.get('max_loss'),
        'max_profit': analysis.get('max_profit'),
        'reasons':    analysis.get('reasons', []),
        'saved_at':   datetime.now(IST).strftime('%I:%M %p IST'),
        'used':       False
    }

    # Extract zone levels from reasons
    for r in analysis.get('reasons', []):
        if 'Order Block' in r or 'OB' in r:
            import re
            nums = re.findall(r'[\d,]+', r.replace(',',''))
            if len(nums) >= 2:
                zone['zone_low']  = float(nums[-2])
                zone['zone_high'] = float(nums[-1])
                break

    # Fallback: use current price ± 0.5%
    if not zone['zone_low']:
        current = analysis.get('current', 58000)
        zone['zone_low']  = round(current * 0.995, 2)
        zone['zone_high'] = round(current * 1.005, 2)

    with open(ZONE_FILE, 'w') as f:
        json.dump(zone, f, indent=2)

    print(f"Zone saved: {zone['bias']} | {zone['zone_low']:,}–{zone['zone_high']:,}")
    return zone


def load_zone() -> dict:
    """Load today's zone saved by evening bot"""
    try:
        if not os.path.exists(ZONE_FILE):
            return None
        with open(ZONE_FILE) as f:
            zone = json.load(f)
        # Only valid if saved today
        today = datetime.now(IST).strftime('%d %b %Y')
        if zone.get('date') != today:
            return None
        if zone.get('used'):
            return None
        return zone
    except:
        return None


def mark_zone_used():
    """Mark zone as used after entry — prevents double entry"""
    try:
        if os.path.exists(ZONE_FILE):
            with open(ZONE_FILE) as f:
                zone = json.load(f)
            zone['used'] = True
            with open(ZONE_FILE, 'w') as f:
                json.dump(zone, f, indent=2)
    except:
        pass


def clear_zone():
    """Clear saved zone"""
    if os.path.exists(ZONE_FILE):
        os.remove(ZONE_FILE)


def is_price_in_zone(current: float, zone: dict,
                     tolerance: float = 0.003) -> bool:
    """
    Check if current price is in or near the saved OB zone.
    tolerance = 0.3% buffer around zone edges
    """
    if not zone:
        return False
    low  = zone['zone_low']  * (1 - tolerance)
    high = zone['zone_high'] * (1 + tolerance)
    return low <= current <= high


def zone_summary(zone: dict) -> str:
    """Human readable zone summary for Telegram"""
    if not zone:
        return "No zone saved"
    return (
        f"{zone['bias']} zone: "
        f"{zone['zone_low']:,}–{zone['zone_high']:,} "
        f"| Score {zone['score']}/10 "
        f"| {zone['name']}"
    )
