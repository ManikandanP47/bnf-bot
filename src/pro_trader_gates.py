"""
Pro trader training gates — sim rehearses the same discipline as /execute.

When SIM_ALIGN_EXECUTE=true (default), virtual trades must pass the core
execute-path filters so July training matches live behavior.
"""

import os

SIM_ALIGN_EXECUTE = os.getenv('SIM_ALIGN_EXECUTE', 'true').lower() == 'true'
SIM_PRO_STRICT = os.getenv('SIM_PRO_STRICT', 'true').lower() == 'true'


def pro_training_gates_active() -> bool:
    return SIM_ALIGN_EXECUTE and SIM_PRO_STRICT


def run_pro_training_gates(setup: dict, params: dict) -> dict:
    """
    Run execute-style gates on a sim setup.
    Returns {ok, reason, gate, gates_passed}.
    """
    if not pro_training_gates_active():
        return {'ok': True, 'gates_passed': ['relaxed']}

    from core.shared_state import STATE

    bias = setup.get('bias', '')
    score = int(setup.get('sim_score', 0) or 0)
    session = setup.get('session', '')
    price = float(setup.get('price', 0) or STATE.get('market.price', 0) or 0)
    vwap = float(STATE.get('market.vwap', 0) or 0)
    flow = STATE.get('market.flow') or STATE.get('signals.market_flow') or {}
    passed = []

    from src.wr_filters import (
        check_min_flow_score, check_vwap_hard, check_shadow_agreement,
        check_oi_wall_veto, check_expiry_week_rules, check_session_win_rate,
        check_premium_sweet_spot,
    )

    for name, result in [
        ('flow', check_min_flow_score(flow, score)),
        ('vwap', check_vwap_hard(price, vwap, bias)),
        ('shadow', check_shadow_agreement(bias)),
        ('oi_wall', check_oi_wall_veto(bias, price)),
        ('expiry_week', check_expiry_week_rules(score)),
        ('session_wr', check_session_win_rate(session)),
        ('sweet_premium', check_premium_sweet_spot(float(params.get('premium', 0) or 0))),
    ]:
        if not result.get('ok', True):
            return {
                'ok': False,
                'reason': result.get('reason', f'{name} blocked'),
                'gate': name,
                'gates_passed': passed,
            }
        passed.append(name)

    from src.greeks_gates import check_iv_rank_for_buyers, check_greeks_for_buyers
    iv = check_iv_rank_for_buyers(score)
    if not iv.get('ok', True):
        return {'ok': False, 'reason': iv['reason'], 'gate': 'iv_rank', 'gates_passed': passed}
    passed.append('iv_rank')

    gk = check_greeks_for_buyers(
        params.get('strike'), params.get('opt_type'), params.get('expiry'),
        premium=float(params.get('premium', 0) or 0),
        session=session,
    )
    if not gk.get('ok', True):
        return {'ok': False, 'reason': gk['reason'], 'gate': 'greeks', 'gates_passed': passed}
    passed.append('greeks')

    try:
        from core.shared_state import STATE
        ctx = STATE.get('market.context') or {}
        if not ctx.get('available'):
            from src.market_context import refresh_market_context
            ctx = refresh_market_context(STATE.get('system.groww_token', ''))
        from src.trading_knowledge import (
            check_level_alignment, check_cpr_alignment, check_theta_context,
        )
        for gate_name, fn in [
            ('pdh_pdl', lambda: check_level_alignment(price, bias, ctx)),
            ('cpr', lambda: check_cpr_alignment(price, bias, ctx)),
            ('theta_ctx', lambda: check_theta_context(ctx)),
        ]:
            res = fn()
            if not res.get('ok', True):
                return {
                    'ok': False,
                    'reason': res.get('reason', gate_name),
                    'gate': gate_name,
                    'gates_passed': passed,
                }
            passed.append(gate_name)
    except Exception:
        pass

    try:
        from src.market_validator import validate_trade
        mv = validate_trade(bias, price)
        if mv.get('blocked') or not mv.get('approved', True):
            return {
                'ok': False,
                'reason': mv.get('block_reason', 'market validator blocked'),
                'gate': 'market_validator',
                'gates_passed': passed,
            }
        passed.append('market_validator')
    except Exception:
        pass

    try:
        from src.pro_loss_prevention import run_pre_trade_loss_prevention, PRO_LOSS_PREVENTION
        if PRO_LOSS_PREVENTION:
            prev = run_pre_trade_loss_prevention(
                {'score': score, 'session': session, 'trend': bias, 'bias': bias},
                params,
            )
            if not prev.get('ok'):
                return {
                    'ok': False,
                    'reason': prev.get('reason', 'loss prevention'),
                    'gate': prev.get('step', 'loss_prevention'),
                    'gates_passed': passed,
                }
            passed.append('loss_prevention')
    except Exception:
        pass

    return {'ok': True, 'gates_passed': passed}


def format_gates_telegram(gate_result: dict) -> str:
    if gate_result.get('ok'):
        n = len(gate_result.get('gates_passed', []))
        return f"✅ Pro gates passed ({n})"
    return f"⛔ Pro gate `{gate_result.get('gate', '?')}` — {gate_result.get('reason', '')[:120]}"
