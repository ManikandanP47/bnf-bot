"""
Trailing Stop Loss — Lock Profits as Trade Moves in Our Favour
After Leg 1 exits, Leg 2 doesn't stay at fixed breakeven.
It TRAILS the price upward, locking more profit.

Real Trader Logic:
  Entry: Rs 265 premium
  Leg 1: Exit at Rs 398 (1.5x) → Rs 931 locked ✅
  
  OLD: Leg 2 SL stays at Rs 265 (breakeven) forever
  NEW: Leg 2 SL TRAILS price up:
    When premium hits Rs 400 → SL moves to Rs 320 (lock Rs 55/unit)
    When premium hits Rs 450 → SL moves to Rs 360 (lock Rs 95/unit)
    When premium hits Rs 500 → SL moves to Rs 400 (lock Rs 135/unit)
    When premium hits Rs 530 → TARGET HIT, exit all ✅

Why this matters:
  OLD exit: If premium goes 265→400→300, exit at breakeven Rs 265 = Rs 0 on Leg 2
  NEW exit: If premium goes 265→400→300, exit at Rs 320 = Rs 55/unit = Rs 385 on Leg 2
  
  You capture MORE profit even when trade doesn't reach full target.
"""

import json
import os
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def calculate_trail_levels(entry_premium: float,
                            trail_pct: float = 0.80) -> list:
    """
    Define trailing SL ladder.
    trail_pct = SL is always X% of the highest premium seen.

    Example with trail_pct=0.80 (SL at 80% of peak):
    Peak Rs 300 → SL Rs 240 (80% of 300)
    Peak Rs 400 → SL Rs 320
    Peak Rs 500 → SL Rs 400
    Peak Rs 530 → TARGET, exit

    We trail only after premium is 20%+ above entry.
    Before that, use breakeven SL.
    """
    trail_start = round(entry_premium * 1.20, 0)  # Start trailing at +20%

    levels = []
    for multiple in [1.2, 1.4, 1.6, 1.8, 2.0]:
        peak    = round(entry_premium * multiple, 0)
        trail_sl = round(peak * trail_pct, 0)
        profit  = round((trail_sl - entry_premium) * 7, 0)  # 7 units in leg 2
        levels.append({
            'peak_at':   peak,
            'trail_sl':  trail_sl,
            'locked_rs': profit
        })

    return levels


def update_trailing_sl(trade_state: dict,
                        current_premium: float) -> dict:
    """
    Updates the trailing SL based on current premium.
    Called every 15 minutes while in trade.

    Returns:
    - new_sl: updated stop loss
    - locked_profit: profit locked so far
    - action: 'UPDATE_SL' / 'EXIT_TARGET' / 'EXIT_TRAIL_SL' / 'HOLD'
    """
    entry    = float(trade_state.get('entry_premium', 0))
    leg1done = trade_state.get('leg1_done', False)
    tgt_prem = float(trade_state.get('tgt_prem', entry * 2))

    if not leg1done:
        # Still on leg 1 — no trailing yet
        return {'action': 'HOLD', 'reason': 'Leg 1 not done yet'}

    # Get current trailing SL (starts at breakeven)
    current_trail_sl = float(trade_state.get('trail_sl', entry))
    peak_premium     = float(trade_state.get('peak_premium', entry))

    # Update peak if current is higher
    new_peak = max(peak_premium, current_premium)

    # Calculate new trail SL (80% of peak, minimum breakeven)
    trail_pct   = 0.80
    raw_trail_sl = new_peak * trail_pct
    new_trail_sl = max(raw_trail_sl, entry)  # Never below breakeven

    # Only move SL UP — never down
    final_trail_sl = max(new_trail_sl, current_trail_sl)
    final_trail_sl = round(final_trail_sl, 0)

    # Profit locked on leg 2 (7 units)
    locked_profit = round((final_trail_sl - entry) * 7, 0)

    # Check target
    if current_premium >= tgt_prem:
        return {
            'action':         'EXIT_TARGET',
            'reason':         f"🎯 Full target Rs {tgt_prem:.0f} hit!",
            'exit_premium':   current_premium,
            'locked_profit':  round((current_premium - entry) * 7, 0)
        }

    # Check trail SL hit
    if current_premium <= final_trail_sl and new_peak > entry * 1.20:
        return {
            'action':         'EXIT_TRAIL_SL',
            'reason':         f"📈 Trail SL hit Rs {final_trail_sl:.0f} (peak was Rs {new_peak:.0f})",
            'exit_premium':   final_trail_sl,
            'locked_profit':  locked_profit
        }

    # Update SL if it moved
    if final_trail_sl > current_trail_sl + 5:  # Meaningful move
        return {
            'action':          'UPDATE_SL',
            'new_trail_sl':    final_trail_sl,
            'old_trail_sl':    current_trail_sl,
            'peak':            new_peak,
            'locked_profit':   locked_profit,
            'reason': (
                f"📈 Trail SL moved to Rs {final_trail_sl:.0f} "
                f"(peak Rs {new_peak:.0f}, locked Rs {locked_profit:,})"
            )
        }

    return {
        'action':          'HOLD',
        'trail_sl':        final_trail_sl,
        'peak':            new_peak,
        'locked_profit':   locked_profit
    }


def get_trail_summary(entry: float) -> str:
    """Show how trailing SL works for a given entry"""
    levels = calculate_trail_levels(entry)
    lines  = [
        f"📈 *Trailing SL Ladder (entry Rs {entry:.0f}):*",
        f"  Start trailing: +20% (Rs {entry*1.2:.0f})",
        ""
    ]
    for l in levels:
        lines.append(
            f"  Peak Rs {l['peak_at']:,.0f} → "
            f"SL Rs {l['trail_sl']:,.0f} → "
            f"Locked Rs {l['locked_rs']:,}"
        )
    return '\n'.join(lines)


if __name__ == '__main__':
    print("Trailing SL Test")
    print("="*50)
    print(get_trail_summary(265))
    print()

    # Simulate a trade
    state = {
        'entry_premium': 265,
        'tgt_prem':      530,
        'leg1_done':     True,
        'trail_sl':      265,
        'peak_premium':  265
    }

    premiums = [280, 320, 360, 400, 450, 420, 380]
    print("Simulating trade:")
    for p in premiums:
        result = update_trailing_sl(state, p)
        if result['action'] == 'UPDATE_SL':
            state['trail_sl']     = result['new_trail_sl']
            state['peak_premium'] = result['peak']
        print(f"  Premium Rs {p} → Action: {result['action']} | {result.get('reason','')[:60]}")
