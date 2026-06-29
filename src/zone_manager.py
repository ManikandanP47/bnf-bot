"""
Zone Manager — The Hybrid Link
Evening bot SAVES the zone.
Morning bot READS and WAITS for price to reach it.
"""

import json
import os
from datetime import datetime, timedelta
import pytz

IST        = pytz.timezone('Asia/Kolkata')
ZONE_FILE  = 'daily_zone.json'


def next_trading_day_str() -> str:
    """Date string for the next NSE session (evening scan → tomorrow)."""
    d = datetime.now(IST).date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime('%d %b %Y')


def today_str() -> str:
    return datetime.now(IST).strftime('%d %b %Y')


def zone_distance_pct(price: float, zone: dict) -> float:
    """How far BNF is from zone midpoint (%). Negative = below zone."""
    if not price or not zone:
        return 0.0
    low  = zone.get('low') or zone.get('zone_low', 0)
    high = zone.get('high') or zone.get('zone_high', 0)
    if not low or not high:
        return 0.0
    mid = (low + high) / 2
    return round((price - mid) / mid * 100, 2)


def zone_to_state(zone: dict) -> dict:
    """Convert saved zone file → shared STATE zone section."""
    return {
        'active':      True,
        'low':         zone.get('zone_low', 0),
        'high':        zone.get('zone_high', 0),
        'bias':        zone.get('bias', 'NEUTRAL'),
        'score':       zone.get('score', 0),
        'option_name': zone.get('name', ''),
        'strike':      zone.get('strike', 0),
        'opt_type':    zone.get('opt_type', 'CE'),
        'expiry':      zone.get('expiry', ''),
        'premium':     zone.get('premium', 265),
        'sl_prem':     zone.get('sl_prem', 186),
        'tgt_prem':    zone.get('tgt_prem', 530),
        'saved_at':    zone.get('saved_at', ''),
        'used':        zone.get('used', False),
    }


def apply_zone_to_state(zone: dict):
    """Hydrate in-memory zone from disk."""
    from core.shared_state import STATE
    if zone and not zone.get('used'):
        STATE.update('zone', zone_to_state(zone))
        print(f"  ✅ Zone loaded: {zone.get('bias')} "
              f"{zone.get('zone_low', 0):,.0f}–{zone.get('zone_high', 0):,.0f}")
    else:
        STATE.set('zone.active', False)
        print("  ℹ️  No active zone for today")


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
        'trade_date':  next_trading_day_str(),
        'date':        datetime.now(IST).strftime('%d %b %Y'),
        'bias':        analysis.get('trend'),
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
    """Load zone valid for today's trading session."""
    try:
        if not os.path.exists(ZONE_FILE):
            return None
        with open(ZONE_FILE) as f:
            zone = json.load(f)
        if zone.get('used'):
            return None
        # Evening scan tags zone for next trading day
        valid = zone.get('trade_date') or zone.get('date')
        if valid != today_str():
            return None
        return zone
    except Exception:
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
