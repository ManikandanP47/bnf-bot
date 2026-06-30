"""
Trader's Playbook — metric math, gate meanings, phase guidance for dashboard.
"""

PLAYBOOK_METRICS = [
    {
        'id': 'rsi',
        'name': 'RSI (5m)',
        'math': '100 − 100/(1 + avg_gain/avg_loss) over 14 candles',
        'use': 'Filter extremes: >70 overbought, <30 oversold. Bot blocks entries at extremes.',
        'trade': 'Use with trend — buy CE when RSI 40–65 in bull zone, not when >70.',
    },
    {
        'id': 'vwap',
        'name': 'VWAP',
        'math': 'Σ(price × volume) / Σ(volume) for session',
        'use': 'Institutional fair price. CE needs price above VWAP; PE below.',
        'trade': 'Pullback to VWAP in trend = high-probability re-entry zone.',
    },
    {
        'id': 'cpr',
        'name': 'CPR (Central Pivot Range)',
        'math': 'P=(H+L+C)/3, BC=(H+L)/2, TC=2P−BC',
        'use': 'Narrow CPR (<0.35%) → trending day. Virgin CPR → strong bias.',
        'trade': 'Break above TC with narrow CPR = bullish day; fade wide CPR edges.',
    },
    {
        'id': 'choch',
        'name': 'CHoCH (Change of Character)',
        'math': '5m close breaks prior swing high (bull) or swing low (bear)',
        'use': 'Confirms structure shift — required on /execute path.',
        'trade': 'Wait for CHoCH inside evening zone, not before.',
    },
    {
        'id': 'flow',
        'name': 'Flow Score',
        'math': 'Sum of VIX regime + PCR + EMA + OI + VWAP + theta signals',
        'use': 'Market weather 0–6+. Execute needs higher flow; sim explores at ≥2.',
        'trade': 'Score <3 = chop. Score 5+ with zone = A+ setup.',
    },
    {
        'id': 'sr',
        'name': 'Support / Resistance',
        'math': 'Swing highs/lows on 5m/15m (3-bar wings)',
        'use': 'Blocks entry within 0.08% of level — avoid buying into walls.',
        'trade': 'CE near resistance = bad R:R. Wait for break + retest.',
    },
    {
        'id': 'theta',
        'name': 'Theta (time decay)',
        'math': 'Premium bleed ∝ 1/DTE × hours_left × premium',
        'use': 'Hard no new entries after 2 PM. 0–2 DTE = gamma risk.',
        'trade': 'Trade morning; afternoon = manage exits only.',
    },
]

PLAYBOOK_TIMING = [
    {'window': '9:15–9:45', 'label': 'Open volatile', 'action': 'Watch only — spreads wide, fake moves'},
    {'window': '9:45–11:30', 'label': 'Morning trend', 'action': 'Best entries — zone + CHoCH + flow'},
    {'window': '11:30–13:00', 'label': 'Lunch chop', 'action': 'Sim may scan; /execute blocked'},
    {'window': '13:00–14:00', 'label': 'Afternoon move', 'action': 'Last window for new /execute'},
    {'window': '14:00–15:10', 'label': 'EOD', 'action': 'Exit only — theta kills small accounts'},
]

PLAYBOOK_TRUST = [
    {
        'metric': 'Sim P&L',
        'trust': 'low',
        'why': 'Relaxed rules (score≥5, fewer filters). Scanner health only.',
    },
    {
        'metric': 'Paper /execute WR',
        'trust': 'high',
        'why': 'Same 15+ filters as live. This is the real exam.',
    },
    {
        'metric': '/readiness gates',
        'trust': 'high',
        'why': '20+ trades, 56% WR, expectancy ₹100+ — blocks live until proven.',
    },
    {
        'metric': 'ML probability',
        'trust': 'medium',
        'why': 'Advisory until 50+ labeled closes. Not a primary gate.',
    },
]


def build_playbook_payload(phase: str = 'SIM') -> dict:
    phase_tips = {
        'SIM': [
            'Week 1–2: bot scans market every ~4 min — learning chart context.',
            'Valid day = ≥3 scans OR ≥1 sim close. Empty days do not count.',
            'Ignore sim win rate; watch scan count + execute gap on dashboard.',
            '/execute is locked — no paper orders yet.',
        ],
        'PAPER': [
            'Week 3–4: only /execute path counts for readiness.',
            'Max 1–2 trades/day. Every trade must pass full risk stack.',
            'Run /readiness weekly. Need 56% WR and ₹100+ expectancy.',
        ],
        'LIVE_READY': [
            'Calendar done — stats must still pass /readiness.',
            'Start with minimum lot. Wednesday min score = 8.',
            'If 2 losses same day, stop — shadow agreement blocks anyway.',
        ],
    }
    return {
        'phase': phase,
        'phase_tips': phase_tips.get(phase, phase_tips['SIM']),
        'metrics': PLAYBOOK_METRICS,
        'timing': PLAYBOOK_TIMING,
        'trust_guide': PLAYBOOK_TRUST,
        'entry_stack': [
            'Evening zone set (~8:15 PM)',
            'Price in zone ±0.6%',
            '15m structure aligns',
            '5m CHoCH confirmed',
            '1m trigger + VWAP side',
            'Flow score + RSI filter',
            'RiskAgent 15+ checks',
            'You /execute',
        ],
        'exit_rules': [
            'Leg 1: book 50% at 1.5× entry premium',
            'Trail SL after peak >1.2× entry',
            'Hard SL at planned stop premium',
            'Force close by 3:10 PM IST',
        ],
        'indian_wisdom': [
            'Paper/sim fills at LTP — live crosses bid-ask spread (1–2% on thin strikes).',
            'Break-even on sim ≈ loss live after brokerage + STT + GST (~₹65/round trip).',
            'BNF is monthly expiry only (last Tuesday) — theta accelerates final week.',
            'No new long options after 2 PM — especially on expiry day.',
            'Daily loss cap 2% (₹100 on ₹5k) — stop terminal when hit.',
            'Premium sweet spot ₹120–₹280 for ₹5k — below = spread kills edge.',
            '70%+ retail option buyers lose — sim WR does not predict live edge.',
            'Trust paper /execute stats + /readiness gates, not virtual gym P&L.',
        ],
    }
