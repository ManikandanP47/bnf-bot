"""
CPR — Central Pivot Range (Indian trader standard).

Pivot P = (H + L + C) / 3
BC      = (H + L) / 2
TC      = 2P − BC
Width   = TC − BC  (narrow → trend day potential)
"""


def compute_cpr(high: float, low: float, close: float) -> dict:
    """CPR from prior session OHLC."""
    if not high or not low or close <= 0:
        return {'available': False}

    pivot = (high + low + close) / 3
    bc    = (high + low) / 2
    tc    = 2 * pivot - bc
    width = tc - bc
    width_pct = (width / pivot * 100) if pivot else 0

    if width_pct < 0.35:
        width_class = 'NARROW'
        width_note  = 'Trend-day potential — breakout bias'
    elif width_pct > 0.75:
        width_class = 'WIDE'
        width_note  = 'Sideways/chop likely — need A+ setup'
    else:
        width_class = 'MEDIUM'
        width_note  = 'Normal CPR — trade with structure'

    return {
        'available':   True,
        'pivot':       round(pivot, 2),
        'bc':          round(bc, 2),
        'tc':          round(tc, 2),
        'width':       round(width, 2),
        'width_pct':   round(width_pct, 3),
        'width_class': width_class,
        'width_note':  width_note,
        'r1':          round(2 * pivot - low, 2),
        'r2':          round(pivot + (high - low), 2),
        's1':          round(2 * pivot - high, 2),
        's2':          round(pivot - (high - low), 2),
    }


def cpr_position(price: float, cpr: dict, today_open: float = 0) -> dict:
    """Where is price vs CPR? Virgin CPR detection."""
    if not cpr.get('available') or not price:
        return {'zone': 'UNKNOWN'}

    tc, bc, p = cpr['tc'], cpr['bc'], cpr['pivot']
    if price > tc:
        zone = 'ABOVE_CPR'
    elif price < bc:
        zone = 'BELOW_CPR'
    elif price >= p:
        zone = 'UPPER_CPR'
    else:
        zone = 'LOWER_CPR'

    virgin = None
    if today_open > 0:
        if today_open > tc and price > tc:
            virgin = 'VIRGIN_BULL'
        elif today_open < bc and price < bc:
            virgin = 'VIRGIN_BEAR'

    return {
        'zone':   zone,
        'virgin': virgin,
        'dist_tc_pct': round((price - tc) / price * 100, 3) if price else 0,
        'dist_bc_pct': round((price - bc) / price * 100, 3) if price else 0,
    }


def format_cpr_report(ctx: dict) -> str:
    cpr = ctx.get('cpr') or {}
    pos = ctx.get('cpr_position') or {}
    if not cpr.get('available'):
        return "📐 *CPR*\n\nNot calculated yet — refreshes with market context."

    lines = [
        f"📐 *CPR — Central Pivot Range*",
        f"━━━━━━━━━━━━━━━━━━━",
        f"TC: {cpr['tc']:,.2f}  ← top of range",
        f"P:  {cpr['pivot']:,.2f}  ← pivot",
        f"BC: {cpr['bc']:,.2f}  ← bottom of range",
        f"",
        f"Width: {cpr['width']:,.0f} pts ({cpr['width_pct']:.2f}%) — *{cpr['width_class']}*",
        f"_{cpr['width_note']}_",
        f"",
        f"R1 {cpr['r1']:,.0f} | R2 {cpr['r2']:,.0f}",
        f"S1 {cpr['s1']:,.0f} | S2 {cpr['s2']:,.0f}",
    ]
    if pos.get('zone'):
        lines += ["", f"*Price position:* {pos['zone'].replace('_', ' ')}"]
    if pos.get('virgin'):
        lines.append(f"*Virgin CPR:* {pos['virgin'].replace('_', ' ')} — strong bias day")
    lines.append("\n_Narrow CPR + virgin open = trend day. Inside CPR = wait for breakout._")
    return '\n'.join(lines)
