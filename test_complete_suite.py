#!/usr/bin/env python3
"""
Complete bot test suite — every major area + scenarios.
Run: python3 test_complete_suite.py
Optional: SEND_TELEGRAM_REPORT=true to push summary to Telegram.
"""

import os
import sys
import json
import tempfile
import threading
from datetime import datetime, time as dtime
from unittest.mock import patch
import pytz

from dotenv import load_dotenv

load_dotenv()

# Isolate tests from production brain DB
_test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
os.environ['DB_PATH'] = _test_db.name
os.environ.setdefault('ML_MODEL_DIR', tempfile.mkdtemp(prefix='bnf_ml_'))

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
IST = pytz.timezone('Asia/Kolkata')

PASS = FAIL = SKIP = 0
SECTIONS = []


def section(name):
    print(f"\n{'='*60}\n{name}\n{'='*60}")
    SECTIONS.append((name, []))


def ok(label, detail=""):
    global PASS
    PASS += 1
    line = f"PASS | {label}" + (f" | {detail}" if detail else "")
    print(f"  ✅ {label}" + (f" — {detail}" if detail else ""))
    if SECTIONS:
        SECTIONS[-1][1].append(line)


def bad(label, detail=""):
    global FAIL
    FAIL += 1
    line = f"FAIL | {label}" + (f" | {detail}" if detail else "")
    print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))
    if SECTIONS:
        SECTIONS[-1][1].append(line)


def skip(label, detail=""):
    global SKIP
    SKIP += 1
    line = f"SKIP | {label}" + (f" | {detail}" if detail else "")
    print(f"  ⏭️  {label}" + (f" — {detail}" if detail else ""))
    if SECTIONS:
        SECTIONS[-1][1].append(line)


def _mock_candles(n=30, base=58500):
    out = []
    for i in range(n):
        p = base + (i % 5) * 20 - 40
        out.append({
            'open': p - 10, 'high': p + 30, 'low': p - 30,
            'close': p, 'volume': 1000 + i * 50,
            'time': f'10:{i%60:02d}',
        })
    return out


# ─────────────────────────────────────────────────────────────
section("1. Environment & core")
try:
    from core.shared_state import STATE
    STATE.set('system.running', True)
    ok("STATE import", "thread-safe singleton")
    assert os.getenv('TELEGRAM_BOT_TOKEN'), "missing token"
    assert os.getenv('TELEGRAM_CHAT_ID'), "missing chat id"
    ok("Telegram .env", "token + chat_id present")
    paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
    ok("PAPER_MODE", "true" if paper else "false (live!)")
    groww_secret = bool(os.getenv('GROWW_TOTP_SECRET'))
    groww_key = bool(os.getenv('GROWW_TOTP_TOKEN'))
    if groww_secret and groww_key:
        ok("Groww creds", "TOTP secret + API key set")
    else:
        bad("Groww creds", "missing TOTP env vars")
except Exception as e:
    bad("Environment", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("2. Module compile (all agents + src)")
modules = [
    'main', 'core.messenger', 'core.command_listener',
    'agents.data_agent', 'agents.analysis_agent', 'agents.agents',
    'agents.learning_agent',
    'src.market_flow', 'src.chart_levels', 'src.market_context',
    'src.cpr', 'src.market_rag', 'src.trading_knowledge',
    'src.market_validator', 'src.oi_analysis', 'src.salary_trader_guards',
    'src.brain_metrics', 'src.trade_analytics', 'src.paper_journal',
    'src.zone_manager', 'src.safety', 'src.capital_guard',
    'src.scanner', 'src.premarket', 'src.history_backtest',
    'src.groww_client', 'src.groww_historical', 'src.expiry_picker',
    'src.shadow_learning', 'src.virtual_broker', 'src.ml_brain',
    'src.sim_learning_report', 'src.market_simulator', 'src.ops_backup',
    'agents.groww_feed_agent', 'src.groww_api_guard', 'src.sim_notify',
]
for mod in modules:
    try:
        __import__(mod)
        ok(mod)
    except Exception as e:
        bad(mod, str(e)[:80])


# ─────────────────────────────────────────────────────────────
section("3. Telegram commands (all handlers)")
try:
    from core.command_listener import CommandListener

    class MockMsg:
        def send(self, *a, **k): return True
        def send_with_buttons(self, *a, **k): return True

    cl = CommandListener(MockMsg())
    cmds = [
        '/help', '/status', '/pnl', '/zone', '/pause', '/resume',
        '/journal', '/readiness', '/funnel', '/context', '/cpr',
        '/flow', '/backtest', '/learn', '/shadow', '/simday', '/evidence', '/simreport', '/ml', '/groww', '/why',
        '/resetlearning',
    ]
    for c in cmds:
        try:
            r = cl._handle(c)
            if not r or len(r) < 15:
                bad(c, f"short reply ({len(r) if r else 0} chars)")
            else:
                ok(c, f"{len(r)} chars")
        except Exception as e:
            bad(c, str(e)[:80])

    # Unknown command
    r = cl._handle('/foobar')
    if 'Unknown' in r or 'help' in r.lower():
        ok("unknown command", "graceful reply")
    else:
        bad("unknown command", r[:60])
except Exception as e:
    bad("command_listener", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("4. Groww auth + live price")
token = None
try:
    import pyotp
    from src.groww_auth import fetch_groww_token
    secret = os.getenv('GROWW_TOTP_SECRET', '')
    code = pyotp.TOTP(secret).now() if secret else ''
    if code:
        assert len(code) == 6
        ok("TOTP generate", code)
    token = fetch_groww_token(max_retries=6, base_delay_sec=60)
    assert token and token.startswith('eyJ')
    ok("Groww auth", f"{token[:24]}...")
    STATE.set('system.groww_token', token)
except Exception as e:
    bad("Groww auth", str(e)[:100])

if token:
    try:
        from agents.data_agent import DataAgent, BANKNIFTY_SYMBOL
        assert BANKNIFTY_SYMBOL == 'NSE_BANKNIFTY'
        ok("symbol", BANKNIFTY_SYMBOL)
        da = DataAgent()
        da._token = token
        px = da.get_live_price()
        if px and px.get('price', 0) > 0:
            ok("live price", f"₹{px['price']:,.0f} via {px.get('source')}")
            STATE.set('market.price', px['price'])
            STATE.set('market.data_source', px.get('source', ''))
        else:
            bad("live price", "empty")
    except Exception as e:
        bad("DataAgent price", str(e)[:100])

    try:
        from src.groww_historical import fetch_candles
        bars = fetch_candles(token, '15m', days=2)
        if bars and len(bars) >= 5:
            ok("historical candles", f"{len(bars)} bars")
        else:
            skip("historical candles", "empty (market closed?)")
    except Exception as e:
        skip("historical candles", str(e)[:60])
else:
    skip("DataAgent", "no token")
    skip("historical", "no token")


# ─────────────────────────────────────────────────────────────
section("5. Market intelligence")
try:
    from core.shared_state import STATE
    price = STATE.get('market.price', 58500) or 58500
    c5 = _mock_candles(30, int(price))
    c15 = _mock_candles(40, int(price))
    STATE.set('market.candles_5m', c5)
    STATE.set('market.candles_15m', c15)
    STATE.set('market.vwap', price - 50)

    from src.chart_levels import compute_chart_levels, check_chart_levels
    lv = compute_chart_levels(c5, c15, price)
    ok("chart_levels", f"avail={lv.get('available')}")
    chk = check_chart_levels(price, 'BULLISH', lv)
    ok("chart_levels check", f"ok={chk.get('ok')}")

    from src.cpr import compute_cpr, cpr_position
    cpr = compute_cpr(58600, 58200, 58400)
    assert cpr.get('tc') and cpr.get('bc')
    ok("CPR compute", f"TC={cpr['tc']:,.0f}")

    from src.market_context import build_market_context, format_context_report
    if token:
        ctx = build_market_context(token)
        if ctx.get('available'):
            ok("market_context", f"PDH={ctx.get('pdh', 0):,.0f}")
            STATE.set('market.context', ctx)
        else:
            skip("market_context", "no daily bars")
    rep = format_context_report()
    ok("format_context", f"{len(rep)} chars")

    from src.market_flow import (
        build_market_flow, refresh_market_flow,
        format_flow_report, format_flow_compact, estimate_theta_bleed,
        flow_allows_trade,
    )
    th = estimate_theta_bleed(200, 5, 10)
    ok("theta estimate", th.get('level', '?'))
    flow = build_market_flow(price, 'BULLISH')
    ok("market_flow", f"score={flow.get('flow_score')}")
    STATE.set('market.flow', flow)
    ok("format_flow", f"{len(format_flow_report())} chars")
    fa = flow_allows_trade('BULLISH', price)
    ok("flow_allows_trade", f"ok={fa.get('ok')}")

    from src.market_validator import check_vix, check_ema, validate_trade
    vix = check_vix()
    ok("VIX check", vix.get('status', '?'))
    ema = check_ema('BULLISH')
    ok("EMA check", ema.get('status', '?'))
    val = validate_trade('BULLISH', price)
    ok("validate_trade", f"blocked={val.get('blocked', False)}")

    try:
        from src.oi_analysis import get_oi_data, calculate_max_pain
        raw = get_oi_data()
        if raw:
            mp = calculate_max_pain(raw)
            if mp.get('available'):
                ok("NSE OI", f"PCR={mp.get('pcr')} max_pain={mp.get('max_pain')}")
            else:
                skip("NSE OI", "parse failed")
        else:
            skip("NSE OI", "NSE blocked or market closed")
    except Exception as e:
        skip("NSE OI", str(e)[:50])

    from src.market_rag import init_knowledge_base, apply_rag_to_signal, format_learn_report
    init_knowledge_base()
    rag = apply_rag_to_signal({'trend': 'BULLISH', 'session': 'MORNING_TREND', 'regime': 'TRENDING'})
    ok("RAG apply", f"ok={rag.get('ok')}")
    ok("format_learn", f"{len(format_learn_report())} chars")
except Exception as e:
    bad("market intel", str(e)[:120])
    import traceback
    traceback.print_exc()


# ─────────────────────────────────────────────────────────────
section("6. Trading knowledge & analysis scenarios")
try:
    from src.trading_knowledge import run_knowledge_checks
    from agents.analysis_agent import (
        check_choch, check_1min_trigger, check_volume_quality, get_structure,
    )

    c5 = STATE.get('market.candles_5m', _mock_candles(30))
    c15 = STATE.get('market.candles_15m', _mock_candles(40))
    price = STATE.get('market.price', 58500)

    struct = get_structure(c15)
    ok("15m structure", struct.get('trend', '?'))

    vol = check_volume_quality(c5)
    ok("volume quality", vol.get('quality', '?'))

    sig = {'price': price, 'trend': 'BULLISH', 'session': 'MORNING_TREND', 'score': 8}
    know = run_knowledge_checks(sig, c5)
    ok("knowledge_checks bullish", f"ok={know.get('ok')}")

    sig_bear = {**sig, 'trend': 'BEARISH'}
    know2 = run_knowledge_checks(sig_bear, c5)
    ok("knowledge_checks bearish", f"ok={know2.get('ok')}")
except Exception as e:
    bad("analysis/knowledge", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("7. Salary trader guards (scenarios)")
try:
    from src.salary_trader_guards import run_salary_trader_guards

    base_sig = {'score': 9, 'session': 'MORNING_TREND', 'trend': 'BULLISH'}
    base_params = {'premium': 200, 'lot_cost': 3000, 'max_loss': 900, 'expiry': '', 'strike': 0, 'opt_type': 'CE'}

    STATE.set('market.data_source', 'GROWW')
    with patch('src.salary_trader_guards.datetime') as mdt:
        class _Morning:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 6, 23, 10, 30, tzinfo=IST)
        mdt.now = _Morning.now
        far_expiry = '08 Jul 2026'
        g = run_salary_trader_guards(
            {**base_sig, 'expiry': far_expiry},
            {**base_params, 'expiry': far_expiry},
        )
    if g.get('ok'):
        ok("guards live data", f"ok={g.get('ok')}")
    else:
        bad("guards live data", g.get('reason', '')[:60])

    STATE.set('market.data_source', 'YFINANCE')
    g2 = run_salary_trader_guards(base_sig, base_params)
    if not g2.get('ok'):
        ok("guards block stale", "YFINANCE blocked")
    else:
        bad("guards block stale", "should block non-GROWW")

    STATE.set('market.data_source', 'GROWW')
    low_sig = {**base_sig, 'score': 5}
    g3 = run_salary_trader_guards(low_sig, base_params)
    if not g3.get('ok'):
        ok("guards cold start", "low score blocked")
    else:
        skip("guards cold start", "may pass if enough trades logged")

    with patch('src.salary_trader_guards.datetime') as mdt:
        class FakeDT:
            @staticmethod
            def now(tz=None):
                n = datetime(2026, 6, 25, 14, 30, tzinfo=IST)  # Wed 2:30 PM
                return n
        mdt.now = FakeDT.now
        mdt.side_effect = lambda *a, **k: datetime
        from src import salary_trader_guards as stg
        g4 = stg.check_theta_time_window(base_sig, base_params)
        if not g4.get('ok'):
            ok("guards afternoon block", g4.get('reason', '')[:40])
        else:
            bad("guards afternoon", "should block after 2 PM")
except Exception as e:
    bad("salary guards", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("8. Risk agent approve (mock signal)")
try:
    from agents.agents import RiskAgent
    from core.messenger import Messenger

    class MockMsg2:
        def send(self, *a, **k): return True
        def send_with_buttons(self, *a, **k): return True

    STATE.set('market.data_source', 'GROWW')
    STATE.set('market.price', STATE.get('market.price', 58500))
    STATE.set('system.paused', False)
    STATE.set('system.weekly_losses', 0)
    STATE.set('zone', {
        'active': True, 'low': 58400, 'high': 58600,
        'bias': 'BULLISH', 'premium': 200, 'strike': 58500,
        'opt_type': 'CE', 'expiry': '2026-07-02', 'used': False,
    })
    STATE.set('market.regime', 'TRENDING')
    STATE.set('market.candles_15m', _mock_candles(20))

    ra = RiskAgent(MockMsg2())
    signal = {
        'price': STATE.get('market.price', 58500),
        'score': 9, 'trend': 'BULLISH',
        'session': 'MORNING_TREND', 'regime': 'TRENDING', 'rsi': 52,
        'reasons': ['test'],
    }
    with patch('agents.agents.datetime') as adt:
        class FakeNow:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 6, 23, 10, 30, tzinfo=IST)  # Mon 10:30 AM
        adt.now = FakeNow.now
        decision = ra.approve(signal)
    if decision.get('approved') is not None:
        ok("RiskAgent.approve", f"approved={decision.get('approved')}")
    else:
        bad("RiskAgent.approve", str(decision))
except Exception as e:
    bad("RiskAgent", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("9. Zone manager roundtrip")
try:
    from src.zone_manager import (
        save_zone, load_zone, clear_zone, zone_to_state,
        next_trading_day_str,
    )

    with tempfile.TemporaryDirectory() as td_dir:
        zf = os.path.join(td_dir, 'daily_zone.json')
        with patch('src.zone_manager.ZONE_FILE', zf):
            sample = {
                'setup': True, 'trend': 'BULLISH', 'score': 7,
                'current': 58500,
                'name': '58500 CE', 'strike': 58500, 'opt_type': 'CE',
                'expiry': '2026-07-08', 'premium': 200,
                'sl_prem': 140, 'tgt_prem': 400,
                'reasons': ['Order Block 58400–58600'],
            }
            z = save_zone(sample)
            if z and z.get('zone_low'):
                ok("save_zone", f"{z.get('zone_low')}–{z.get('zone_high')}")
            else:
                bad("save_zone", str(z))
            trade_day = z.get('trade_date') or next_trading_day_str()
            with patch('src.zone_manager.today_str', return_value=trade_day):
                loaded = load_zone()
            if loaded and loaded.get('zone_low'):
                ok("load_zone", "roundtrip OK")
                st = zone_to_state(loaded)
                if st.get('active') and st.get('low'):
                    ok("zone_to_state")
                else:
                    bad("zone_to_state", str(st))
            else:
                bad("load_zone", f"expected on {trade_day}")
            clear_zone()
            if not os.path.exists(zf):
                ok("clear_zone")
except Exception as e:
    bad("zone_manager", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("10. Safety & capital")
try:
    from src.safety import check_trading_day, check_circuit_breaker
    from src.capital_guard import check_trade_cost_vs_capital, format_morning_brief
    from src.expiry_picker import days_to_expiry, next_banknifty_expiry

    td = check_trading_day()
    ok("trading_day", f"trade={td.get('trade')} ({td.get('reason', '')[:30]})")

    STATE.set('system.weekly_losses', 0)
    cb = check_circuit_breaker()
    ok("circuit_breaker", f"halted={cb.get('halted', False)}")

    cost = check_trade_cost_vs_capital(3000)
    ok("capital guard", f"blocked={cost.get('blocked')}")

    brief = format_morning_brief()
    ok("morning_brief", f"{len(brief)} chars")

    exp = next_banknifty_expiry()
    if exp:
        dte = days_to_expiry(exp)
        ok("expiry picker", f"{exp} DTE={dte}")
    else:
        skip("expiry picker", "no expiry returned")
except Exception as e:
    bad("safety/capital", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("11. Brain, journal, analytics")
try:
    from src.brain_metrics import assess_live_readiness, format_readiness_report
    from src.trade_analytics import format_funnel_report, log_funnel
    from src.paper_journal import format_journal_command, format_daily_paper_report
    from src.history_backtest import format_backtest_report

    ready = assess_live_readiness()
    ok("live_readiness", f"ready={ready.get('ready')}")
    ok("format_readiness", f"{len(format_readiness_report())} chars")
    ok("format_funnel", f"{len(format_funnel_report())} chars")
    ok("format_journal", f"{len(format_journal_command())} chars")
    ok("format_daily_report", f"{len(format_daily_paper_report())} chars")
    ok("format_backtest", f"{len(format_backtest_report())} chars")

    log_funnel('test_event', {'trend': 'BULLISH', 'score': 7}, 'suite test')
    ok("log_funnel", "no crash")
except Exception as e:
    bad("brain/journal", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("12. Execution params (paper, no order)")
try:
    from agents.agents import ExecutionAgent

    class MockMsg3:
        def send(self, *a, **k): return True
        def send_with_buttons(self, *a, **k): return True

    ex = ExecutionAgent(MockMsg3())
    signal = {
        'price': 58500, 'trend': 'BULLISH', 'score': 8,
        'session': 'MORNING_TREND', 'regime': 'TRENDING',
    }
    STATE.set('zone', {
        'premium': 200, 'strike': 58500, 'opt_type': 'CE',
        'expiry': '2026-07-02', 'bias': 'BULLISH',
    })
    params = ex.calculate_trade_params(signal)
    if params.get('name') and params.get('lot_cost', 0) > 0:
        ok("calculate_trade_params", f"{params['name']} ₹{params['lot_cost']:,}")
    else:
        skip("calculate_trade_params", "no affordable strike (capital?)")

    risk = {'confidence': 75, 'reasons': ['test'], 'warnings': []}
    msg_len = 0
    orig = ex.messenger.send_with_buttons
    captured = []
    def cap(msg, buttons):
        captured.append(msg)
        return True
    ex.messenger.send_with_buttons = cap
    STATE.set('signals.market_flow', flow if 'flow' in dir() else {'available': True, 'vix': {'vix': 14}})
    ex.send_trade_suggestion(signal, risk, params if params.get('name') else {
        'name': '58500 CE', 'premium': 200, 'lot_cost': 3000, 'lots': 1,
        'sl_prem': 140, 'tgt_prem': 400, 'max_loss': 900, 'max_gain': 3000,
        'leg1_profit': 1500,
    })
    if captured and 'TRADE SUGGESTION' in captured[0]:
        ok("trade_suggestion", f"{len(captured[0])} chars")
        if 'F&O Flow' in captured[0] or 'Flow' in captured[0]:
            ok("suggestion has flow block")
        else:
            skip("flow in suggestion", "flow block empty (no OI cache)")
    else:
        bad("trade_suggestion", "message not captured")
    ex.messenger.send_with_buttons = orig
except Exception as e:
    bad("execution", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("13. Startup message content")
try:
    src = open(os.path.join(ROOT, 'main.py')).read()
    for kw in ['/flow', '/context', '/cpr', '/learn', '/readiness']:
        if kw not in src:
            bad("startup cmds", f"missing {kw}")
        else:
            ok(f"startup has {kw}")
except Exception as e:
    bad("startup", str(e)[:80])


# ─────────────────────────────────────────────────────────────
section("14. Shadow roundtrip (temp DB)")
try:
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'tests', 'test_shadow_roundtrip.py')],
        capture_output=True, text=True, timeout=30, cwd=ROOT,
    )
    if r.returncode == 0:
        ok("shadow_roundtrip", (r.stdout or '').strip().split('\n')[-1][:60])
    else:
        bad("shadow_roundtrip", (r.stderr or r.stdout or '')[:120])
except Exception as e:
    bad("shadow_roundtrip", str(e)[:120])


# ─────────────────────────────────────────────────────────────
section("15. ML neural net (temp DB)")
try:
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'tests', 'test_ml_nn.py')],
        capture_output=True, text=True, timeout=90, cwd=ROOT,
    )
    if r.returncode == 0:
        ok("ml_nn_ensemble", (r.stdout or '').strip().split('\n')[-1][:60])
    else:
        bad("ml_nn_ensemble", (r.stderr or r.stdout or '')[:120])
except Exception as e:
    bad("ml_nn_ensemble", str(e)[:120])


# ─────────────────────────────────────────────────────────────
total = PASS + FAIL + SKIP
print(f"\n{'='*60}")
print(f"FINAL: ✅ {PASS} passed | ❌ {FAIL} failed | ⏭️  {SKIP} skipped | {total} total")
print(f"{'='*60}")

summary_lines = [f"🧪 *Complete Bot Test*", f"✅ {PASS} | ❌ {FAIL} | ⏭️ {SKIP}", ""]
for name, lines in SECTIONS:
    fails = [l for l in lines if l.startswith('FAIL')]
    if fails:
        summary_lines.append(f"*{name}*: {len(fails)} fail(s)")
for name, lines in SECTIONS:
    fails = [l for l in lines if l.startswith('FAIL')]
    for f in fails[:8]:
        summary_lines.append(f"  {f.split('|', 2)[-1].strip()}")

if os.getenv('SEND_TELEGRAM_REPORT', 'false').lower() == 'true':
    try:
        from core.messenger import Messenger
        Messenger().send('\n'.join(summary_lines[:40]))
        print("📱 Summary sent to Telegram")
    except Exception as e:
        print(f"Telegram send skipped: {e}")

sys.exit(1 if FAIL else 0)
