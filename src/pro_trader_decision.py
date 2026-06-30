"""
Pro trader decision engine — how a desk thinks before clicking buy.

July sim/paper month: bot maps index ranges, scans the full chain (CE + PE),
classifies strike archetypes, ranks by R:R, compares sides, and logs spread /
theta alternatives so it *adapts* to the market — not just repeats one rule.

Live ₹5k: still long-only execution; spreads / naked sells are training intel.
"""

import os
import json
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
LOT_SIZE = 15
STRIKE_GAP = 100

PRO_CHAIN_SCAN = os.getenv('PRO_CHAIN_SCAN', 'true').lower() == 'true'
PRO_SPREAD_TRAINING = os.getenv('PRO_SPREAD_TRAINING', 'true').lower() == 'true'
PRO_THETA_ADVISORY = os.getenv('PRO_THETA_ADVISORY', 'true').lower() == 'true'
PRO_SIM_SIDE_FLIP = os.getenv('PRO_SIM_SIDE_FLIP', 'false').lower() == 'true'
CHAIN_DEPTH = int(os.getenv('PRO_LADDER_DEPTH', '12'))
SL_PCT = float(os.getenv('PRO_SL_PCT', '0.28'))
TGT_MUL = float(os.getenv('PRO_TGT_MUL', '2.0'))

# What a pro checks (dashboard + learning log)
PRO_CHECKLIST_META = [
    {'id': 'session', 'name': 'Session & timing', 'hint': 'No lunch chop / late theta'},
    {'id': 'ranges', 'name': 'Index ranges', 'hint': 'PDH PDL VWAP CPR OI walls'},
    {'id': 'structure', 'name': 'Market structure', 'hint': '15m bias + 5m CHoCH'},
    {'id': 'flow', 'name': 'F&O flow', 'hint': 'VIX PCR EMA OI composite'},
    {'id': 'ce_pe', 'name': 'CE vs PE today', 'hint': 'Which side has edge'},
    {'id': 'strike_type', 'name': 'Strike archetype', 'hint': 'Sweet OTM not lottery'},
    {'id': 'chain_rr', 'name': 'Chain-wide R:R', 'hint': 'Best reward per rupee risk'},
    {'id': 'greeks', 'name': 'Greeks & IV', 'hint': 'Delta theta IV rank'},
    {'id': 'oi_pin', 'name': 'OI / max pain', 'hint': 'Pin risk & walls'},
    {'id': 'spread_alt', 'name': 'Spread alternative', 'hint': 'Would pro use vertical?'},
    {'id': 'theta_mode', 'name': 'Theta strategy', 'hint': 'Buy vs sell premium'},
    {'id': 'capital', 'name': 'Capital fit', 'hint': 'Wallet + daily cap'},
]


def _dist_pct(price: float, level: float) -> float:
    if not price or not level:
        return 999.0
    return round(abs(price - level) / price * 100, 3)


def _near(price: float, level: float, pct: float = 0.12) -> bool:
    return _dist_pct(price, level) <= pct


def classify_strike_archetype(strike: int, opt_type: str, bnf_price: float) -> dict:
    """LOTTERY_OTM | SWEET_OTM | ATM | ITM"""
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    if opt_type.upper() in ('CE', 'CALL'):
        otm_steps = max(0, (strike - atm) // STRIKE_GAP)
        itm_steps = max(0, (atm - strike) // STRIKE_GAP)
    else:
        otm_steps = max(0, (atm - strike) // STRIKE_GAP)
        itm_steps = max(0, (strike - atm) // STRIKE_GAP)

    if itm_steps >= 1:
        archetype = 'ITM'
        label = f'{itm_steps} ITM — high delta, expensive'
    elif otm_steps == 0:
        archetype = 'ATM'
        label = 'ATM — balanced delta/gamma'
    elif otm_steps <= 3:
        archetype = 'SWEET_OTM'
        label = f'{otm_steps} OTM — pro buyer sweet spot'
    elif otm_steps <= 5:
        archetype = 'DEEP_OTM'
        label = f'{otm_steps} OTM — needs big move'
    else:
        archetype = 'LOTTERY_OTM'
        label = f'{otm_steps} OTM — lottery ticket'

    return {
        'archetype': archetype,
        'label': label,
        'otm_steps': otm_steps,
        'itm_steps': itm_steps,
    }


def compute_strike_rr(premium: float, sl_pct: float = None, tgt_mul: float = None) -> dict:
    sl_pct = sl_pct if sl_pct is not None else SL_PCT
    tgt_mul = tgt_mul if tgt_mul is not None else TGT_MUL
    if premium <= 0:
        return {'max_loss': 0, 'max_gain': 0, 'rr_ratio': 0, 'rr_label': '—'}
    max_loss = round(premium * sl_pct * LOT_SIZE, 0)
    max_gain = round(premium * (tgt_mul - 1) * LOT_SIZE, 0)
    rr = round(max_gain / max_loss, 2) if max_loss > 0 else 0
    return {
        'max_loss': max_loss,
        'max_gain': max_gain,
        'rr_ratio': rr,
        'rr_label': f'1:{rr:.1f}' if rr else '—',
        'sl_prem': round(premium * (1 - sl_pct), 0),
        'tgt_prem': round(premium * tgt_mul, 0),
    }


def build_index_range_map(price: float = None) -> dict:
    """PDH, PDL, VWAP, CPR, OI walls — the pro's map of the battlefield."""
    from core.shared_state import STATE

    price = float(price or STATE.get('market.price', 0) or 0)
    vwap = float(STATE.get('market.vwap', 0) or 0)
    ctx = STATE.get('market.context') or {}
    if not ctx.get('available'):
        try:
            from src.market_context import refresh_market_context
            token = STATE.get('system.groww_token', '')
            ctx = refresh_market_context(token)
        except Exception:
            ctx = {}

    levels = []
    if ctx.get('available'):
        for key, name, role in [
            ('pdh', 'PDH', 'resistance'),
            ('pdl', 'PDL', 'support'),
            ('pdc', 'PDC', 'pivot'),
            ('pwh', 'PWH', 'week_high'),
            ('pwl', 'PWL', 'week_low'),
        ]:
            val = ctx.get(key, 0)
            if val:
                levels.append({
                    'id': key, 'name': name, 'value': val,
                    'dist_pct': _dist_pct(price, val),
                    'role': role,
                    'near': _near(price, val),
                    'above': price > val if price else None,
                })
        cpr = ctx.get('cpr') or {}
        if cpr.get('available'):
            for key, name in [('tc', 'CPR TC'), ('pivot', 'CPR P'), ('bc', 'CPR BC')]:
                val = cpr.get(key if key != 'pivot' else 'pivot', 0)
                if val:
                    levels.append({
                        'id': f'cpr_{key}', 'name': name, 'value': val,
                        'dist_pct': _dist_pct(price, val),
                        'role': 'cpr',
                        'near': _near(price, val),
                        'above': price > val if price else None,
                    })

    if vwap > 0:
        levels.append({
            'id': 'vwap', 'name': 'VWAP', 'value': round(vwap, 2),
            'dist_pct': _dist_pct(price, vwap),
            'role': 'intraday_fair',
            'near': _near(price, vwap, 0.05),
            'above': price > vwap if price else None,
        })

    try:
        from src.oi_analysis import calculate_max_pain
        from src.oi_analysis import get_oi_data
        raw = get_oi_data()
        mp = calculate_max_pain(raw) if raw else {}
        if mp.get('available'):
            for key, name, role in [
                ('resistance', 'CE OI wall', 'oi_resistance'),
                ('support', 'PE OI wall', 'oi_support'),
                ('max_pain_strike', 'Max pain', 'max_pain'),
            ]:
                val = mp.get(key, 0)
                if val:
                    levels.append({
                        'id': key, 'name': name, 'value': val,
                        'dist_pct': _dist_pct(price, val),
                        'role': role,
                        'near': _dist_pct(price, val) < 0.25,
                        'above': price > val if price else None,
                    })
    except Exception:
        pass

    levels.sort(key=lambda x: x.get('dist_pct', 999))
    nearest = levels[0] if levels else None
    between = []
    if price and len(levels) >= 2:
        below = [l for l in levels if l.get('above') is False]
        above = [l for l in levels if l.get('above') is True]
        if below and above:
            between = [below[0]['name'], above[0]['name']]

    return {
        'ok': bool(levels),
        'price': price,
        'levels': levels[:12],
        'nearest': nearest,
        'trading_between': between,
        'cpr_position': (ctx.get('cpr_position') or {}).get('zone', ''),
        'theta_risk': (ctx.get('theta') or {}).get('level', ''),
    }


def _scan_side_strikes(bnf_price: float, opt_type: str, expiry: str,
                       max_cost: float) -> list:
    from src.premium_feed import fetch_option_ltp
    from src.pro_strike_scan import _score_strike, SWEET_MIN, SWEET_MAX

    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    strikes = set()
    if opt_type == 'CE':
        for i in range(-1, CHAIN_DEPTH + 1):
            strikes.add(atm + STRIKE_GAP * i)
    else:
        for i in range(-1, CHAIN_DEPTH + 1):
            strikes.add(atm - STRIKE_GAP * i)

    rows = []
    bias = 'BULLISH' if opt_type == 'CE' else 'BEARISH'
    for strike in sorted(strikes):
        if strike <= 0:
            continue
        ltp = fetch_option_ltp(strike, opt_type, expiry)
        if ltp <= 0:
            continue
        lot_cost = ltp * LOT_SIZE
        base = _score_strike(strike, opt_type, ltp, bnf_price, bias, lot_cost, max_cost)
        if not base:
            continue
        arch = classify_strike_archetype(strike, opt_type, bnf_price)
        rr = compute_strike_rr(ltp)
        composite = base['score']
        if arch['archetype'] == 'SWEET_OTM':
            composite += 2
        elif arch['archetype'] == 'LOTTERY_OTM':
            composite -= 3
        elif arch['archetype'] == 'ITM':
            composite += 0
        composite += min(3, int(rr['rr_ratio']))  # R:R boost

        row = dict(base)
        row.update({
            'archetype': arch['archetype'],
            'archetype_label': arch['label'],
            'rr_ratio': rr['rr_ratio'],
            'rr_label': rr['rr_label'],
            'composite_score': composite,
        })
        rows.append(row)

    rows.sort(key=lambda x: (-x['composite_score'], -x['rr_ratio']))
    return rows


def scan_full_option_chain(bnf_price: float, expiry: str, capital: float = None) -> dict:
    """Scan CE + PE ladders — chain-wide best R:R."""
    from src.pro_strike_scan import _max_lot_cost

    if not bnf_price or not expiry:
        return {'ok': False, 'reason': 'no price/expiry'}

    max_cost = _max_lot_cost(capital)
    ce = _scan_side_strikes(bnf_price, 'CE', expiry, max_cost)
    pe = _scan_side_strikes(bnf_price, 'PE', expiry, max_cost)
    combined = sorted(ce + pe, key=lambda x: (-x['composite_score'], -x['rr_ratio']))
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP

    result = {
        'ok': bool(combined),
        'atm': atm,
        'expiry': expiry,
        'max_lot_cost': round(max_cost, 0),
        'ce_count': len(ce),
        'pe_count': len(pe),
        'ce_best': ce[0] if ce else None,
        'pe_best': pe[0] if pe else None,
        'chain_best': combined[0] if combined else None,
        'chain_top5': combined[:5],
        'ce_top3': ce[:3],
        'pe_top3': pe[:3],
        'ts': datetime.now(IST).strftime('%H:%M:%S'),
    }
    return result


def decide_ce_vs_pe(bnf_price: float, structure_bias: str, expiry: str,
                    capital: float = None) -> dict:
    """
  Pro habit: both sides exist same day — score which has better edge now.
  Structure bias is primary; log when chain disagrees.
    """
    chain = scan_full_option_chain(bnf_price, expiry, capital)
    best_ce = chain.get('ce_best') or {}
    best_pe = chain.get('pe_best') or {}
    ce_comp = best_ce.get('composite_score', 0)
    pe_comp = best_pe.get('composite_score', 0)
    ce_rr = best_ce.get('rr_ratio', 0)
    pe_rr = best_pe.get('rr_ratio', 0)

    if ce_comp >= pe_comp + 2:
        edge_side = 'CE'
        edge_note = f'CE stronger today (score {ce_comp} vs PE {pe_comp}, R:R {ce_rr} vs {pe_rr})'
    elif pe_comp >= ce_comp + 2:
        edge_side = 'PE'
        edge_note = f'PE stronger today (score {pe_comp} vs CE {ce_comp}, R:R {pe_rr} vs {ce_rr})'
    else:
        edge_side = 'NEUTRAL'
        edge_note = f'Both sides similar — follow structure ({structure_bias})'

    struct_side = 'CE' if structure_bias == 'BULLISH' else 'PE'
    conflict = (
        edge_side in ('CE', 'PE')
        and edge_side != struct_side
        and abs(ce_comp - pe_comp) >= 2
    )

    trade_side = struct_side
    flip_note = ''
    if conflict and PRO_SIM_SIDE_FLIP and max(ce_comp, pe_comp) >= 6:
        trade_side = edge_side
        flip_note = f'Sim flip: structure {structure_bias} but {edge_side} edge stronger'
    elif conflict:
        flip_note = (
            f'Structure says {struct_side} but {edge_side} scores better — '
            f'pro would wait or hedge'
        )

    return {
        'structure_bias': structure_bias,
        'edge_side': edge_side,
        'trade_side': trade_side,
        'recommended_opt': trade_side,
        'conflict': conflict,
        'edge_note': edge_note,
        'flip_note': flip_note,
        'best_ce': best_ce,
        'best_pe': best_pe,
        'chain': chain,
    }


def suggest_vertical_spread(long_row: dict, bnf_price: float, expiry: str) -> dict:
    """
    Bull call / bear put vertical — training intel (not executed on ₹5k live).
    Pro reduces cost vs naked long.
    """
    if not PRO_SPREAD_TRAINING or not long_row:
        return {'available': False}

    from src.premium_feed import fetch_option_ltp

    opt = long_row.get('opt_type', 'CE')
    long_k = int(long_row.get('strike') or 0)
    long_prem = float(long_row.get('premium') or 0)
    if not long_k or not long_prem:
        return {'available': False}

    width = STRIKE_GAP * 2
    if opt == 'CE':
        short_k = long_k + width
        spread_type = 'BULL_CALL_SPREAD'
    else:
        short_k = long_k - width
        spread_type = 'BEAR_PUT_SPREAD'

    short_prem = fetch_option_ltp(short_k, opt, expiry)
    if short_prem <= 0:
        return {'available': False, 'reason': 'short leg LTP missing'}

    net_debit = round((long_prem - short_prem) * LOT_SIZE, 0)
    max_profit = round((width - (long_prem - short_prem)) * LOT_SIZE, 0)
    max_loss = net_debit
    rr = round(max_profit / max_loss, 2) if max_loss > 0 else 0

    naked_loss = long_row.get('max_loss', long_prem * SL_PCT * LOT_SIZE)
    capital_save = round(max(0, naked_loss - max_loss), 0)

    return {
        'available': True,
        'spread_type': spread_type,
        'training_only': True,
        'long_leg': f'BANKNIFTY {long_k} {opt} @ ₹{long_prem:.0f}',
        'short_leg': f'BANKNIFTY {short_k} {opt} @ ₹{short_prem:.0f}',
        'net_debit_rs': net_debit,
        'max_profit_rs': max_profit,
        'max_loss_rs': max_loss,
        'rr_label': f'1:{rr:.1f}',
        'vs_naked': f'Saves ~₹{capital_save:,} max risk vs naked long',
        'note': 'July training — observe spread math; live ₹5k stays long-only',
    }


def suggest_protective_hedge(primary: dict, bnf_price: float, expiry: str) -> dict:
    """When long CE, pros often hold small PE as tail hedge (training log)."""
    if not primary or not PRO_SPREAD_TRAINING:
        return {'available': False}
    opt = primary.get('opt_type', 'CE')
    hedge_opt = 'PE' if opt == 'CE' else 'CE'
    atm = round(bnf_price / STRIKE_GAP) * STRIKE_GAP
    hedge_strike = atm - STRIKE_GAP if hedge_opt == 'PE' else atm + STRIKE_GAP
    from src.premium_feed import fetch_option_ltp
    prem = fetch_option_ltp(hedge_strike, hedge_opt, expiry)
    if prem <= 0:
        return {'available': False}
    return {
        'available': True,
        'training_only': True,
        'hedge': f'BANKNIFTY {hedge_strike} {hedge_opt} @ ₹{prem:.0f}',
        'hedge_cost_rs': round(prem * LOT_SIZE, 0),
        'note': f'Pro tail hedge if holding {primary.get("name")} into event',
    }


def theta_strategy_advisory() -> dict:
    """When IV high + chop — pros sell premium; ₹5k observes only."""
    from core.shared_state import STATE

    if not PRO_THETA_ADVISORY:
        return {'mode': 'BUY', 'note': ''}

    ch = STATE.get('market.option_chain') or {}
    iv_rank = float(ch.get('iv_rank', 50) or 50)
    regime = STATE.get('market.regime', 'TRENDING')
    session = STATE.get('market.session', '')

    sell_signals = 0
    reasons = []
    if iv_rank >= 70:
        sell_signals += 2
        reasons.append(f'IV rank {iv_rank:.0f} elevated')
    if regime in ('RANGING', 'CHOP'):
        sell_signals += 2
        reasons.append(f'regime {regime}')
    if session in ('LUNCH_CHOP', 'EOD_CHOP'):
        sell_signals += 1
        reasons.append('chop session')

    if sell_signals >= 3:
        return {
            'mode': 'SELL_PREMIUM_ADVISORY',
            'execute': False,
            'strategies': ['credit_spread', 'iron_condor', 'short_strangle'],
            'reasons': reasons,
            'note': (
                'Pro desk may *sell* OTM premium here — your ₹5k bot *buys* only. '
                'Logged for market adaptation; no naked shorts in July.'
            ),
        }
    if iv_rank <= 35:
        return {
            'mode': 'BUY_PREMIUM_FAVORED',
            'execute': True,
            'reasons': [f'IV rank {iv_rank:.0f} cheap for buyers'],
            'note': 'Favour long CE/PE — IV expansion tailwind',
        }
    return {'mode': 'BUY', 'execute': True, 'reasons': [], 'note': ''}


def run_pro_checklist(bias: str, price: float, params: dict,
                      setup_score: int, session: str) -> dict:
    """Full pre-trade checklist — pass/fail per pro habit."""
    from core.shared_state import STATE

    checks = []
    ok_all = True

    def _add(cid, name, ok, detail):
        nonlocal ok_all
        if not ok:
            ok_all = False
        checks.append({'id': cid, 'name': name, 'ok': bool(ok), 'detail': detail[:200]})

    chop = session in ('LUNCH_CHOP', 'EOD_CHOP', 'OPEN_VOLATILE', 'PRE_MARKET')
    _add('session', 'Session & timing', not chop,
         'OK' if not chop else f'Avoid new entries in {session}')

    ranges = build_index_range_map(price)
    _add('ranges', 'Index ranges', ranges.get('ok'),
         f"{len(ranges.get('levels', []))} levels mapped" if ranges.get('ok')
         else 'Context loading')

    struct = STATE.get('market.structure_15m', STATE.get('signals.structure', ''))
    struct_ok = (bias == 'BULLISH' and 'BULL' in str(struct).upper()) or \
                (bias == 'BEARISH' and 'BEAR' in str(struct).upper()) or not struct
    _add('structure', 'Market structure', struct_ok or setup_score >= 7,
         f'15m {struct or "?"} vs {bias}')

    flow = STATE.get('market.flow') or {}
    fs = flow.get('flow_score', 0)
    _add('flow', 'F&O flow', fs >= 3 or setup_score >= 8, f'Flow {fs}/6')

    expiry = params.get('expiry', '')
    ce_pe = decide_ce_vs_pe(price, bias, expiry)
    _add('ce_pe', 'CE vs PE today', not ce_pe.get('conflict') or setup_score >= 9,
         ce_pe.get('edge_note', ''))

    arch = classify_strike_archetype(
        params.get('strike') or 0, params.get('opt_type', 'CE'), price)
    arch_ok = arch['archetype'] not in ('LOTTERY_OTM',) if params.get('strike') else True
    _add('strike_type', 'Strike archetype', arch_ok, arch['label'])

    rr = compute_strike_rr(float(params.get('premium', 0) or 0))
    _add('chain_rr', 'Chain-wide R:R', rr['rr_ratio'] >= 1.5,
         f"R:R {rr['rr_label']} (₹{rr['max_gain']:,} / ₹{rr['max_loss']:,})")

    try:
        from src.greeks_gates import check_greeks_for_buyers, check_iv_rank_for_buyers
        gk = check_greeks_for_buyers(
            params.get('strike'), params.get('opt_type'), expiry,
            premium=float(params.get('premium', 0) or 0), session=session,
        )
        iv = check_iv_rank_for_buyers(setup_score)
        _add('greeks', 'Greeks & IV', gk.get('ok') and iv.get('ok'),
             gk.get('reason', '')[:80])
    except Exception as e:
        _add('greeks', 'Greeks & IV', True, str(e)[:40])

    try:
        from src.max_pain_filter import check_max_pain_pin
        pin = check_max_pain_pin(bias, price)
        _add('oi_pin', 'OI / max pain', pin.get('ok', True), pin.get('reason', 'clear')[:80])
    except Exception:
        _add('oi_pin', 'OI / max pain', True, 'check skipped')

    spread = suggest_vertical_spread(
        {'strike': params.get('strike'), 'opt_type': params.get('opt_type'),
         'premium': params.get('premium'), 'max_loss': rr['max_loss']},
        price, expiry,
    )
    _add('spread_alt', 'Spread alternative', True,
         spread.get('vs_naked', spread.get('note', 'n/a'))[:80] if spread.get('available')
         else 'Naked long OK for size')

    theta = theta_strategy_advisory()
    theta_ok = theta.get('mode') != 'SELL_PREMIUM_ADVISORY' or setup_score >= 9
    _add('theta_mode', 'Theta strategy', theta_ok,
         theta.get('note', theta.get('mode', ''))[:100])

    try:
        from src.sim_wallet import plan_sim_order
        plan = plan_sim_order(float(params.get('premium', 0) or 0))
        _add('capital', 'Capital fit', plan.get('ok'), plan.get('reason', 'wallet OK')[:80])
    except Exception:
        _add('capital', 'Capital fit', True, 'wallet check skipped')

    return {
        'ok': ok_all,
        'passed': sum(1 for c in checks if c['ok']),
        'total': len(checks),
        'checks': checks,
    }


def build_pro_decision(bias: str = None, price: float = None,
                       expiry: str = None, params: dict = None,
                       setup_score: int = 0, session: str = '') -> dict:
    """Master decision payload — stored in STATE for dashboard + learning."""
    from core.shared_state import STATE

    price = float(price or STATE.get('market.price', 0) or 0)
    zone = STATE.get('zone', {}) or {}
    bias = bias or zone.get('bias', 'BULLISH')
    expiry = expiry or zone.get('expiry', '')
    if not expiry:
        try:
            from src.expiry_picker import next_banknifty_expiry
            expiry = next_banknifty_expiry(5)
        except Exception:
            pass
    session = session or STATE.get('market.session', '')

    ranges = build_index_range_map(price)
    chain = scan_full_option_chain(price, expiry) if PRO_CHAIN_SCAN and price and expiry else {}
    ce_pe = decide_ce_vs_pe(price, bias, expiry) if price and expiry else {}

    pick = None
    if params and params.get('strike'):
        pick = dict(params)
        arch = classify_strike_archetype(pick['strike'], pick.get('opt_type', 'CE'), price)
        rr = compute_strike_rr(float(pick.get('premium', 0) or 0))
        pick.update(arch)
        pick.update(rr)
    elif chain.get('chain_best'):
        side = ce_pe.get('trade_side', 'CE')
        pick = chain.get('ce_best') if side == 'CE' else chain.get('pe_best')
        if not pick:
            pick = chain.get('chain_best')

    spread = suggest_vertical_spread(pick or {}, price, expiry) if pick else {}
    hedge = suggest_protective_hedge(pick or {}, price, expiry) if pick else {}
    theta = theta_strategy_advisory()
    checklist = run_pro_checklist(
        bias, price, pick or params or {}, setup_score, session,
    )

    decision = {
        'ok': bool(price),
        'ts': datetime.now(IST).strftime('%H:%M:%S'),
        'price': price,
        'bias': bias,
        'session': session,
        'ranges': ranges,
        'chain': chain,
        'ce_pe': ce_pe,
        'pick': pick,
        'spread': spread,
        'hedge': hedge,
        'theta_advisory': theta,
        'checklist': checklist,
        'checklist_meta': PRO_CHECKLIST_META,
    }

    try:
        STATE.set('market.pro_decision', decision)
    except Exception:
        pass
    return decision


def pick_chain_best_rr(bnf_price: float, bias: str, expiry: str,
                       capital: float = None) -> dict:
    """Best strike for sim/execute — chain R:R + structure side."""
    ce_pe = decide_ce_vs_pe(bnf_price, bias, expiry, capital)
    side = ce_pe.get('trade_side', 'CE' if bias == 'BULLISH' else 'PE')
    chain = ce_pe.get('chain') or scan_full_option_chain(bnf_price, expiry, capital)
    pick = chain.get('ce_best') if side == 'CE' else chain.get('pe_best')
    if not pick:
        pick = chain.get('chain_best')
    if not pick:
        from src.pro_strike_scan import pick_pro_strike
        return pick_pro_strike(bnf_price, bias, expiry, capital) or {}

    out = dict(pick)
    out['expiry'] = expiry
    out['prem_source'] = 'PRO_CHAIN_RR'
    out['ce_pe_note'] = ce_pe.get('edge_note', '')
    out['flip_note'] = ce_pe.get('flip_note', '')
    if ce_pe.get('conflict'):
        out['structure_conflict'] = True
    return out


def build_pro_decision_dashboard() -> dict:
    try:
        from core.shared_state import STATE
        d = STATE.get('market.pro_decision')
        if d:
            return d
        return build_pro_decision()
    except Exception:
        return {'ok': False}


def format_pro_decision_telegram(decision: dict) -> str:
    if not decision.get('ok'):
        return 'Pro decision — waiting for market data'
    lines = [
        '🧠 *Pro trader map*',
        f"Spot {decision.get('price', 0):,.0f} | {decision.get('bias')} | {decision.get('session')}",
    ]
    r = decision.get('ranges') or {}
    if r.get('nearest'):
        n = r['nearest']
        lines.append(f"Nearest: {n['name']} {n['value']:,.0f} ({n['dist_pct']:.2f}%)")
    cp = decision.get('ce_pe') or {}
    if cp.get('edge_note'):
        lines.append(f"CE/PE: {cp['edge_note']}")
    pick = decision.get('pick') or {}
    if pick.get('name'):
        lines.append(
            f"Pick: {pick['name']} ₹{pick.get('premium')} "
            f"R:R {pick.get('rr_label', '?')} · {pick.get('archetype', '')}"
        )
    cl = decision.get('checklist') or {}
    lines.append(f"Checklist: {cl.get('passed', 0)}/{cl.get('total', 12)}")
    th = decision.get('theta_advisory') or {}
    if th.get('note'):
        lines.append(f"_{th['note'][:120]}_")
    return '\n'.join(lines)
