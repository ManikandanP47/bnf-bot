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
    """Current market as an analysis-style signal."""
    return {
        'price': STATE.get('market.price', 0),
        'score': 8,
        'trend': STATE.get('zone', {}).get('bias', 'NEUTRAL'),
        'session': STATE.get('market.session', ''),
        'regime': STATE.get('market.regime', 'TRENDING'),
        'rsi': STATE.get('market.rsi_5m', 50),
    }


def format_execute_gap_summary() -> str:
    from src.market_simulator import evaluate_explore_setup, SIM_MIN_SCORE

    sim = evaluate_explore_setup()
    sig = build_signal_from_market()
    if sig.get('trend') not in ('BULLISH', 'BEARISH'):
        sig['trend'] = sim.get('bias') or 'NEUTRAL'
    if sim.get('sim_score'):
        sig['score'] = max(sig.get('score', 5), sim.get('sim_score', 5) + 3)

    risk = would_execute_approve(sig)
    sim_ok = sim.get('ok', False)
    lines = [
        f"  Sim min score: *{SIM_MIN_SCORE}* → "
        f"{'✅ pass' if sim_ok else '❌ ' + str(sim.get('reason', 'fail'))}",
        f"  Execute/Risk path → "
        f"{'✅ would approve' if risk.get('approved') else '❌ ' + str(risk.get('reason', 'block'))[:60]}",
    ]
    if sim_ok and not risk.get('approved'):
        lines.append(
            "  _Sim can trade while /execute would block — expected in week 1–2_"
        )
    return '\n'.join(lines)
