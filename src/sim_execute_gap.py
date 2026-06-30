"""
Sim vs Execute gap — would today's sim setup pass strict /execute filters?
"""

from core.shared_state import STATE


def would_execute_approve(signal: dict) -> dict:
    """Run RiskAgent.approve on a synthetic signal from sim context."""
    try:
        from agents.risk_agent import RiskAgent
        from core.messenger import Messenger
        ra = RiskAgent(Messenger())
        return ra.approve(signal)
    except Exception as e:
        return {'approved': False, 'reason': f'check failed: {e}'[:80]}


def build_signal_from_market() -> dict:
    """Current market as an analysis-style signal (no score inflation)."""
    sig = STATE.get('signals.analysis') or {}
    zone = STATE.get('zone', {}) or {}
    return {
        'price': STATE.get('market.price', 0),
        'score': sig.get('score', 5),
        'trend': sig.get('trend') or zone.get('bias', 'NEUTRAL'),
        'session': STATE.get('market.session', ''),
        'regime': STATE.get('market.regime', 'TRENDING'),
        'rsi': STATE.get('market.rsi_5m', 50),
    }


def build_execute_gap_payload() -> dict:
    """Structured sim vs execute comparison for dashboard."""
    from src.market_simulator import evaluate_explore_setup, SIM_MIN_SCORE

    sim = evaluate_explore_setup()
    sig = build_signal_from_market()
    if sig.get('trend') not in ('BULLISH', 'BEARISH'):
        bias = sim.get('bias')
        if bias in ('BULLISH', 'BEARISH'):
            sig['trend'] = bias

    risk = would_execute_approve(sig)
    sim_ok = sim.get('ok', False)
    exec_ok = risk.get('approved', False)
    misleading = sim_ok and not exec_ok

    return {
        'sim_min_score': SIM_MIN_SCORE,
        'sim_ok': sim_ok,
        'sim_reason': sim.get('reason', ''),
        'sim_score': sim.get('sim_score', 0),
        'sim_reasons': sim.get('reasons', []),
        'execute_ok': exec_ok,
        'execute_reason': risk.get('reason', ''),
        'signal_score': sig.get('score', 0),
        'bias': sim.get('bias') or sig.get('trend', ''),
        'misleading': misleading,
        'verdict': (
            'Sim and execute agree — setup is real.'
            if sim_ok and exec_ok else
            'Sim can trade but /execute would block — do not trust sim P&L alone.'
            if misleading else
            'Neither path would trade right now.'
            if not sim_ok and not exec_ok else
            'Execute stricter than sim — normal in training.'
        ),
    }


def format_execute_gap_summary() -> str:
    gap = build_execute_gap_payload()
    sim_ok = gap['sim_ok']
    risk_ok = gap['execute_ok']
    lines = [
        f"  Sim min score: *{gap['sim_min_score']}* → "
        f"{'✅ pass' if sim_ok else '❌ ' + str(gap['sim_reason'])[:50]}",
        f"  Execute/Risk path → "
        f"{'✅ would approve' if risk_ok else '❌ ' + str(gap['execute_reason'])[:60]}",
    ]
    if gap['misleading']:
        lines.append(
            "  _Sim can trade while /execute would block — expected in week 1–2_"
        )
    return '\n'.join(lines)
