"""
Pro loss prevention & rectification — how desks keep loss rare, small, and reviewed.

Pros know loss *will* happen; they prevent it with process:
  PRE-TRADE  → don't take bad risk (caps, streaks, premortem)
  IN-TRADE   → cut losers early (MAE kill, flow fade, leg-1, trail, time stop)
  POST-LOSS  → diagnose, pause, one disciplined recovery — never revenge size

July sim/paper runs all three layers on every scan and every open position.
"""

import os
from datetime import datetime, time as dtime, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

PRO_LOSS_PREVENTION = os.getenv('PRO_LOSS_PREVENTION', 'true').lower() == 'true'
MAX_DAILY_LOSS_TRADES = int(os.getenv('MAX_DAILY_LOSS_TRADES', '2'))
MAE_KILL_PCT = float(os.getenv('MAE_KILL_PCT', '65'))
MAE_KILL_MINUTES = int(os.getenv('MAE_KILL_MINUTES', '15'))
TIME_STOP_MINUTES = int(os.getenv('TIME_STOP_MINUTES', '105'))
REVENGE_COOLDOWN_MIN = int(os.getenv('REVENGE_COOLDOWN_MIN', '45'))
PREMORTEM_MAX_DAILY_PCT = float(os.getenv('PREMORTEM_MAX_DAILY_PCT', '40'))

PRE_TRADE_STEPS = [
    {'id': 'daily_cap', 'name': 'Daily loss cap', 'hint': 'Stop when day budget gone'},
    {'id': 'weekly_cap', 'name': 'Weekly loss cap', 'hint': 'Pause until Monday'},
    {'id': 'loss_count', 'name': 'Max losses/day', 'hint': '2 losses = done for day'},
    {'id': 'consec_week', 'name': 'Consecutive loss pause', 'hint': '2 in a row = auto pause'},
    {'id': 'premortem', 'name': 'Pre-mortem risk', 'hint': 'One trade cannot kill the day'},
    {'id': 'capital_risk', 'name': 'SL vs capital', 'hint': 'Max 25% of ₹5k at SL'},
    {'id': 'revenge_cool', 'name': 'Revenge cooldown', 'hint': 'Wait after a loss'},
    {'id': 'data_live', 'name': 'Live data only', 'hint': 'No delayed feed entries'},
    {'id': 'theta_window', 'name': 'Theta time window', 'hint': 'No late-day lottery'},
    {'id': 'shadow_block', 'name': 'Shadow agreement', 'hint': 'Skip if virtual edge says no'},
]

IN_TRADE_STEPS = [
    {'id': 'leg1_lock', 'name': 'Leg-1 @ 1.5×', 'hint': 'Book half — free trade on rest'},
    {'id': 'trail_sl', 'name': 'Trail leg-2', 'hint': '80% of peak — lock profit'},
    {'id': 'mae_kill', 'name': 'MAE kill switch', 'hint': 'Cut if loss hits fast'},
    {'id': 'flow_fade', 'name': 'Flow fade exit', 'hint': 'Exit when F&O context dies'},
    {'id': 'time_stop', 'name': 'Time stop', 'hint': 'No dead trade into theta'},
    {'id': 'eod_flat', 'name': 'EOD flat', 'hint': 'Out by 3:10 — no overnight options'},
]

POST_LOSS_STEPS = [
    {'id': 'diagnose', 'name': 'Diagnose mistake', 'hint': 'SL tight? timing? chop?'},
    {'id': 'pause', 'name': 'Pause same session', 'hint': 'No immediate re-entry'},
    {'id': 'journal', 'name': 'Log lesson', 'hint': 'Brain + recovery drill'},
    {'id': 'drill', 'name': 'Virtual recovery drill', 'hint': 'Sim rehearses afternoon'},
    {'id': 'recovery_slot', 'name': 'One recovery slot', 'hint': 'A+ afternoon only'},
    {'id': 'no_size_up', 'name': 'Never size up', 'hint': 'Same 1 lot after loss'},
    {'id': 'weekly_cap_r', 'name': 'Weekly recovery cap', 'hint': 'Max 2 recoveries/week'},
]


def _conn():
    from src.db_persistence import connect
    return connect()


def _today() -> str:
    return datetime.now(IST).strftime('%Y-%m-%d')


def count_today_losses(include_sim: bool = True) -> dict:
    """Closed losses today — real + optional sim."""
    today = _today()
    real = sim = 0
    try:
        conn = _conn()
        row = conn.execute("""
            SELECT COUNT(*) FROM trades
            WHERE date=? AND outcome='LOSS'
        """, (today,)).fetchone()
        real = int(row[0] or 0)
        if include_sim:
            row2 = conn.execute("""
                SELECT COUNT(*) FROM shadow_trades
                WHERE date=? AND status='CLOSED' AND outcome='LOSS'
            """, (today,)).fetchone()
            sim = int(row2[0] or 0)
        conn.close()
    except Exception:
        pass
    return {'real': real, 'sim': sim, 'total': real + sim}


def _last_loss_minutes_ago() -> float:
    """Minutes since last closed loss (real or sim)."""
    today = _today()
    last_time = None
    try:
        conn = _conn()
        for q in (
            "SELECT exit_time FROM trades WHERE date=? AND outcome='LOSS' ORDER BY id DESC LIMIT 1",
            "SELECT exit_time FROM shadow_trades WHERE date=? AND outcome='LOSS' ORDER BY id DESC LIMIT 1",
        ):
            row = conn.execute(q, (today,)).fetchone()
            if row and row[0]:
                last_time = row[0]
                break
        conn.close()
    except Exception:
        pass
    if not last_time:
        return 9999.0
    try:
        now = datetime.now(IST)
        t = datetime.strptime(f"{today} {last_time}", '%Y-%m-%d %H:%M')
        t = IST.localize(t) if t.tzinfo is None else t
        return max(0, (now - t).total_seconds() / 60)
    except Exception:
        return 9999.0


def check_daily_loss_count(include_sim: bool = False) -> dict:
    """Pro rule: 2 losses same day → stop trading."""
    c = count_today_losses(include_sim=include_sim)
    n = c['total'] if include_sim else c['real']
    if n >= MAX_DAILY_LOSS_TRADES:
        return {
            'ok': False,
            'reason': (
                f"🛑 {n} loss(es) today — pro stops after "
                f"{MAX_DAILY_LOSS_TRADES}. Protect capital, come tomorrow."
            ),
            'losses_today': n,
        }
    return {'ok': True, 'losses_today': n}


def check_revenge_cooldown(is_recovery: bool = False) -> dict:
    """Block rapid re-entry after a loss (recovery path exempt)."""
    if is_recovery:
        return {'ok': True}
    mins = _last_loss_minutes_ago()
    if mins < REVENGE_COOLDOWN_MIN:
        wait = int(REVENGE_COOLDOWN_MIN - mins)
        return {
            'ok': False,
            'reason': (
                f"⏸ Revenge cooldown — wait {wait}m after last loss. "
                f"Pros pause; they don't double-click."
            ),
        }
    return {'ok': True, 'minutes_since_loss': round(mins, 0)}


def check_premortem_risk(max_loss_rs: float) -> dict:
    """One trade must not consume too much of daily loss budget."""
    from src.capital_guard import MAX_DAILY_LOSS_RS
    from src.sim_wallet import effective_daily_loss_cap
    try:
        from src.shadow_learning import training_phase
        cap = effective_daily_loss_cap() if training_phase() == 'SIM' else MAX_DAILY_LOSS_RS
    except Exception:
        cap = MAX_DAILY_LOSS_RS
    limit = cap * PREMORTEM_MAX_DAILY_PCT / 100
    if max_loss_rs > limit:
        return {
            'ok': False,
            'reason': (
                f"🛑 Pre-mortem: SL risk ₹{max_loss_rs:,.0f} > "
                f"{PREMORTEM_MAX_DAILY_PCT:.0f}% of daily cap (₹{limit:,.0f}). "
                f"One loss cannot blow the day."
            ),
        }
    return {'ok': True, 'daily_cap_rs': cap, 'max_allowed_rs': round(limit, 0)}


def run_pre_trade_loss_prevention(signal: dict, params: dict = None,
                                  is_recovery: bool = False) -> dict:
    """
    All pre-trade loss prevention — call from RiskAgent + sim gates.
    Returns {ok, reason, step, steps_passed, steps}.
    """
    if not PRO_LOSS_PREVENTION:
        return {'ok': True, 'steps_passed': ['disabled']}

    params = params or {}
    passed = []

    from src.capital_guard import check_daily_loss_cap, check_weekly_loss_cap
    from core.shared_state import STATE

    for step_id, fn in [
        ('daily_cap', lambda: check_daily_loss_cap()),
        ('weekly_cap', lambda: check_weekly_loss_cap()),
    ]:
        r = fn()
        if r.get('blocked'):
            return {'ok': False, 'reason': r['reason'], 'step': step_id, 'steps_passed': passed}
        passed.append(step_id)

    try:
        from src.shadow_learning import training_phase
        sim_phase = training_phase() == 'SIM'
    except Exception:
        sim_phase = False

    if sim_phase:
        from src.sim_wallet import is_account_dead_today
        dead = is_account_dead_today()
        if dead.get('dead'):
            return {
                'ok': False,
                'reason': dead.get('reason', 'sim account dead today'),
                'step': 'daily_cap',
                'steps_passed': passed,
            }
        passed.append('sim_wallet_cap')
    else:
        r = check_daily_loss_count(include_sim=False)
        if not r.get('ok'):
            return {'ok': False, 'reason': r['reason'], 'step': 'loss_count', 'steps_passed': passed}
        passed.append('loss_count')

    weekly_losses = STATE.get('system.weekly_losses', 0)
    if weekly_losses >= 2:
        return {
            'ok': False,
            'reason': '🛑 2 consecutive losses this week — paused until review',
            'step': 'consec_week',
            'steps_passed': passed,
        }
    passed.append('consec_week')

    max_loss = float(params.get('max_loss', 0) or 0)
    if not max_loss:
        prem = float(params.get('premium', 0) or 0)
        max_loss = prem * 0.30 * 15
    pm = check_premortem_risk(max_loss)
    if not pm.get('ok'):
        return {'ok': False, 'reason': pm['reason'], 'step': 'premortem', 'steps_passed': passed}
    passed.append('premortem')

    from src.salary_trader_guards import check_capital_at_risk
    cap_r = check_capital_at_risk(signal, params)
    if not cap_r.get('ok'):
        return {'ok': False, 'reason': cap_r['reason'], 'step': 'capital_risk', 'steps_passed': passed}
    passed.append('capital_risk')

    rc = check_revenge_cooldown(is_recovery=is_recovery)
    if not rc.get('ok'):
        return {'ok': False, 'reason': rc['reason'], 'step': 'revenge_cool', 'steps_passed': passed}
    passed.append('revenge_cool')

    from src.salary_trader_guards import check_data_quality, check_theta_time_window
    for step_id, fn in [
        ('data_live', check_data_quality),
        ('theta_window', check_theta_time_window),
    ]:
        r = fn(signal, params)
        if not r.get('ok', True):
            return {'ok': False, 'reason': r['reason'], 'step': step_id, 'steps_passed': passed}
        passed.append(step_id)

    bias = signal.get('trend', signal.get('bias', ''))
    if bias:
        from src.wr_filters import check_shadow_agreement
        sh = check_shadow_agreement(bias)
        if not sh.get('ok', True) and not is_recovery:
            return {'ok': False, 'reason': sh['reason'], 'step': 'shadow_block', 'steps_passed': passed}
        passed.append('shadow_block')

    return {'ok': True, 'steps_passed': passed, 'losses_today': count_today_losses()}


def evaluate_in_trade_pro_exit(
    entry_prem: float,
    est_prem: float,
    entry_time: str,
    sl_prem: float,
    max_loss_rs: float,
    entry_flow: int,
    cur_flow: int,
    leg1_done: bool = False,
    pnl_rs: float = 0,
) -> dict:
    """
    Pro in-trade exits — MAE kill, time stop, flow fade.
    Returns {exit, reason, action, step}.
    """
    if not PRO_LOSS_PREVENTION or entry_prem <= 0:
        return {'exit': False}

    now = datetime.now(IST)
    today = _today()
    hold_m = 0
    if entry_time:
        try:
            et = datetime.strptime(f"{today} {entry_time}", '%Y-%m-%d %H:%M')
            et = IST.localize(et) if et.tzinfo is None else et
            hold_m = (now - et).total_seconds() / 60
        except Exception:
            pass

    planned_loss = max_loss_rs or max((entry_prem - sl_prem) * 15, entry_prem * 0.28 * 15)

    if not leg1_done and hold_m <= MAE_KILL_MINUTES and planned_loss > 0:
        if pnl_rs <= -(planned_loss * MAE_KILL_PCT / 100):
            return {
                'exit': True,
                'step': 'mae_kill',
                'action': 'CUT_LOSS',
                'reason': (
                    f"⚡ MAE kill — loss ₹{abs(pnl_rs):,.0f} hit "
                    f"{MAE_KILL_PCT:.0f}% of plan in {hold_m:.0f}m. Pro cuts fast."
                ),
            }

    if not leg1_done and hold_m >= TIME_STOP_MINUTES and est_prem < entry_prem * 1.15:
        return {
            'exit': True,
            'step': 'time_stop',
            'action': 'CUT_STALE',
            'reason': (
                f"⏳ Time stop — {hold_m:.0f}m without leg-1 progress. "
                f"Theta eats buyers; pros don't hope."
            ),
        }

    if (entry_flow or 0) >= 3 and cur_flow <= (entry_flow - 2) and pnl_rs < 0:
        return {
            'exit': True,
            'step': 'flow_fade',
            'action': 'CUT_FLOW',
            'reason': (
                f"📉 Flow faded {entry_flow}→{cur_flow} while red — "
                f"context gone, exit."
            ),
        }

    if now.time() >= dtime(15, 10):
        return {
            'exit': True,
            'step': 'eod_flat',
            'action': 'EOD',
            'reason': '⏰ EOD flat — no holding options into close',
        }

    return {'exit': False}


def build_rectification_plan(loss_data: dict) -> dict:
    """Structured steps pros take after a loss."""
    pnl = float(loss_data.get('pnl_rs', 0) or 0)
    try:
        from src.loss_recovery import classify_from_exit, _is_recoverable, RECOVERY_SL_PCT
        loss_type = loss_data.get('mistake_type') or classify_from_exit(loss_data)
        recoverable = _is_recoverable(loss_type, pnl)
    except Exception:
        loss_type = 'UNKNOWN'
        recoverable = False
        RECOVERY_SL_PCT = 0.22

    steps = []
    steps.append({
        'step': 1,
        'action': 'STOP',
        'title': 'Stop trading this session',
        'detail': 'No immediate re-entry — revenge is how ₹5k dies',
        'done': True,
    })
    steps.append({
        'step': 2,
        'action': 'DIAGNOSE',
        'title': f'Diagnose: {loss_type}',
        'detail': loss_data.get('lesson', loss_data.get('reason', ''))[:120] or 'Review chart + gates',
        'done': True,
    })
    steps.append({
        'step': 3,
        'action': 'JOURNAL',
        'title': 'Log to brain + recovery drill',
        'detail': 'Pattern memory updates — same mistake harder next time',
        'done': True,
    })
    if recoverable:
        steps.append({
            'step': 4,
            'action': 'WAIT',
            'title': 'Wait for AFTERNOON_MOVE',
            'detail': 'Fresh session only — never same hour as loss',
            'done': False,
        })
        steps.append({
            'step': 5,
            'action': 'RECOVERY',
            'title': 'One recovery slot IF score ≥ 9',
            'detail': f'Same 1 lot · tighter SL {RECOVERY_SL_PCT*100:.0f}% · no size up',
            'done': False,
        })
    else:
        steps.append({
            'step': 4,
            'action': 'WALK',
            'title': 'Not recoverable — walk away today',
            'detail': 'Bad session / chop / low score — capital > ego',
            'done': True,
        })

    steps.append({
        'step': 6,
        'action': 'RULE',
        'title': 'Never increase size after loss',
        'detail': 'Pros shrink or stay flat — never martingale',
        'done': True,
    })

    return {
        'loss_pnl': pnl,
        'loss_type': loss_type,
        'recoverable': recoverable,
        'steps': steps,
        'next_action': steps[3]['title'] if len(steps) > 3 else 'Review',
    }


def on_loss_rectification(loss_data: dict, source: str = 'SIM') -> dict:
    """
    Full post-loss hook — recovery window + rectification plan + STATE.
    Called from monitor (real) and shadow (sim).
    """
    from src.loss_recovery import (
        activate_recovery_window, format_recovery_telegram_after_loss,
    )

    rec = activate_recovery_window(loss_data, source=source)
    plan = build_rectification_plan(loss_data)
    payload = {
        'recovery': rec,
        'rectification': plan,
        'source': source,
        'ts': datetime.now(IST).strftime('%H:%M:%S'),
    }

    try:
        from core.shared_state import STATE
        STATE.set('market.loss_rectification', payload)
    except Exception:
        pass

    try:
        from agents.learning_agent import BRAIN
        lt = plan.get('loss_type', 'LOSS')
        key = f"loss_prevent:{lt}"
        BRAIN._record_observe_key(key, good_avoid=0, today=_today())
    except Exception:
        pass

    payload['telegram'] = format_loss_rectification_telegram(rec, plan)
    return payload


def format_loss_rectification_telegram(recovery_ctx: dict, plan: dict) -> str:
    lines = [
        '🛡️ *Pro Loss Rectification*',
        f"Loss: ₹{abs(plan.get('loss_pnl', 0)):,.0f} · `{plan.get('loss_type', '?')}`",
        '',
        '*Prevention next time:*',
        '  • Tighter entry gates already logged',
        '  • Brain pattern updated',
        '',
    ]
    for s in plan.get('steps', [])[:5]:
        icon = '✅' if s.get('done') else '⏳'
        lines.append(f"{icon} {s.get('title', '')}")
    if recovery_ctx.get('recoverable'):
        lines.append('')
        lines.append(format_recovery_telegram_after_loss({
            'recoverable': True,
            'loss_type': plan.get('loss_type'),
            'loss_pnl': plan.get('loss_pnl'),
        }))
    else:
        lines.append('')
        lines.append('_Not recoverable — protect capital, sim still drills._')
    lines.append('')
    lines.append('Type /recovery · Dashboard → Loss Prevention')
    return '\n'.join(lines)


def format_recovery_telegram_after_loss(ctx: dict) -> str:
    from src.loss_recovery import format_recovery_telegram_after_loss as _fmt
    return _fmt(ctx)


def build_loss_prevention_dashboard() -> dict:
    """Dashboard payload — caps, streaks, rectification, playbooks."""
    from core.shared_state import STATE

    losses = count_today_losses()
    rect = STATE.get('market.loss_rectification') or {}
    recovery = {}
    try:
        from src.loss_recovery import recovery_status
        recovery = recovery_status()
    except Exception:
        pass

    daily_cap = weekly_cap = {}
    try:
        from src.capital_guard import check_daily_loss_cap, check_weekly_loss_cap
        daily_cap = check_daily_loss_cap()
        weekly_cap = check_weekly_loss_cap()
    except Exception:
        pass

    sim_dead = {}
    try:
        from src.sim_wallet import is_account_dead_today, wallet_core
        sim_dead = is_account_dead_today()
        wallet = wallet_core()
    except Exception:
        wallet = {}

    return {
        'enabled': PRO_LOSS_PREVENTION,
        'losses_today': losses,
        'max_daily_loss_trades': MAX_DAILY_LOSS_TRADES,
        'minutes_since_loss': round(_last_loss_minutes_ago(), 0),
        'revenge_cooldown_min': REVENGE_COOLDOWN_MIN,
        'daily_cap_blocked': daily_cap.get('blocked', False),
        'weekly_cap_blocked': weekly_cap.get('blocked', False),
        'weekly_consec_losses': STATE.get('system.weekly_losses', 0),
        'paused': STATE.get('system.paused', False),
        'sim_account_dead': sim_dead.get('dead', False),
        'sim_daily_cap_rs': sim_dead.get('cap_rs'),
        'wallet_balance': wallet.get('balance'),
        'recovery': recovery,
        'last_rectification': rect,
        'pre_trade_steps': PRE_TRADE_STEPS,
        'in_trade_steps': IN_TRADE_STEPS,
        'post_loss_steps': POST_LOSS_STEPS,
        'mae_kill': {'pct': MAE_KILL_PCT, 'minutes': MAE_KILL_MINUTES},
        'time_stop_minutes': TIME_STOP_MINUTES,
    }
