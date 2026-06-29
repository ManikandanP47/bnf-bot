"""
Market RAG — local retrieval + learning (no paid LLM required).

Stores trading rules + lessons from your paper/live trades.
On each setup, retrieves the most relevant knowledge and adjusts score.

Optional: set OPENAI_API_KEY for LLM summary (disabled by default on 512MB VPS).
"""

import os
import sqlite3
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')

SEED_CHUNKS = [
    ("cpr,narrow,bullish", "RULE", "Narrow CPR + open above TC often becomes trend day. Favour CE only with structure + volume.", 1.0),
    ("cpr,narrow,bearish", "RULE", "Narrow CPR + open below BC often becomes bear trend day. Favour PE with breakdown confirmation.", 1.0),
    ("cpr,wide,chop", "RULE", "Wide CPR = balance/chop. Skip marginal setups; need score 8+ and clear CHoCH.", 1.0),
    ("cpr,inside,upper", "RULE", "Price in upper CPR = decision zone. Wait for hold above TC for CE; fakeouts common.", 0.9),
    ("cpr,inside,lower", "RULE", "Price in lower CPR = decision zone. Wait for hold below BC for PE.", 0.9),
    ("cpr,virgin,bull", "RULE", "Virgin CPR bull: opened above TC and never re-entered CPR — strong bullish bias until TC breaks.", 1.0),
    ("cpr,virgin,bear", "RULE", "Virgin CPR bear: opened below BC — strong bearish bias until BC reclaims.", 1.0),
    ("theta,afternoon", "RULE", "After 2 PM option buying bleeds theta even if direction right. Salary ₹5k accounts avoid late entries.", 1.0),
    ("theta,wednesday", "RULE", "Wednesday BNF expiry: gamma spikes. Prefer morning only; afternoon blocked.", 1.0),
    ("smc,choch,bullish", "RULE", "5m CHoCH above pullback high = sellers exhausted. Must align with 15m structure.", 0.95),
    ("smc,zone,pullback", "RULE", "Evening zone is magnet next day. Enter on pullback TO zone, not chase away from it.", 0.95),
    ("capital,small", "RULE", "₹5k capital: one lot SL must stay under 25% of account. Cheap OTM better than ATM lottery.", 1.0),
    ("spread,thin", "RULE", "Premium under ₹100 on BNF options = wide spread. Paper fills lie; live slippage kills edge.", 0.9),
    ("session,morning", "RULE", "9:45–11:30 MORNING_TREND = best window for BNF directional option buys.", 0.85),
    ("session,afternoon", "RULE", "Afternoon entries need extra confirmation — theta accelerates after lunch.", 0.85),
    ("regime,ranging", "RULE", "RANGING regime: brain should block or require score 9+. Mean reversion fakes CHoCH.", 0.9),
    ("mistake,THETA_DECAY", "LOSS", "Theta decay loss: direction OK but held too long. Exit by 2:30 PM or use tighter trail.", 1.0),
    ("mistake,CHASE", "LOSS", "Chased entry away from zone — premium paid too much. Wait for pullback.", 1.0),
    ("win,TARGET", "WIN", "Full target hit: setup + session + zone aligned. Brain should repeat this pattern combo.", 0.9),
    ("oi,pcr,bullish", "RULE", "PCR > 1.2 with CE OI wall overhead = cautious CE. Wait for break above highest CALL OI strike.", 0.95),
    ("oi,pcr,bearish", "RULE", "PCR < 0.7 = crowded puts. PE into heavy PUT support often bounces — need breakdown confirmation.", 0.95),
    ("oi,maxpain", "RULE", "Price far from max pain into expiry = pin risk. Salary accounts avoid lottery bets near expiry.", 0.9),
    ("oi,wall,resistance", "RULE", "Highest CALL OI strike = institutional resistance. Do not buy CE with target above CE wall without breakout.", 1.0),
    ("chart,resistance", "RULE", "Auto 15m swing resistance within 0.1% = do not chase CE. Wait for close above line.", 0.95),
    ("chart,support", "RULE", "Auto 15m swing support within 0.1% = do not chase PE. Wait for close below line.", 0.95),
    ("vix,high", "RULE", "VIX > 20 = premium expensive + whipsaw. Skip or require score 9+.", 0.95),
]


def _conn():
    return sqlite3.connect(DB_FILE)


def init_knowledge_base():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tags TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT 'seed',
            outcome TEXT DEFAULT 'RULE',
            weight REAL DEFAULT 1.0,
            uses INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    n = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    if n == 0:
        now = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        for tags, outcome, content, weight in SEED_CHUNKS:
            conn.execute(
                "INSERT INTO knowledge_chunks (tags, content, outcome, source, weight, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (tags, content, outcome, 'seed', weight, now),
            )
    conn.commit()
    conn.close()


def _tags_from_state(state: dict) -> set:
    tags = set()
    for key in ('session', 'regime'):
        v = state.get(key, '')
        if v:
            tags.add(str(v).lower())
    bias = state.get('trend', '') or state.get('bias', '')
    if bias:
        tags.add(str(bias).lower())

    ctx = state.get('context') or {}
    cpr = ctx.get('cpr') or {}
    pos = ctx.get('cpr_position') or {}
    if cpr.get('width_class'):
        tags.add('cpr')
        tags.add(cpr['width_class'].lower())
    if pos.get('zone'):
        z = pos['zone'].lower()
        tags.add('cpr')
        if 'upper' in z:
            tags.update({'inside', 'upper'})
        elif 'lower' in z:
            tags.update({'inside', 'lower'})
        elif 'above' in z:
            tags.add('bullish')
        elif 'below' in z:
            tags.add('bearish')
    if pos.get('virgin'):
        tags.add('virgin')
        if 'BULL' in pos['virgin']:
            tags.update({'bull', 'bullish'})
        if 'BEAR' in pos['virgin']:
            tags.update({'bear', 'bearish'})
    return tags


def _score_chunk(chunk_tags: str, query_tags: set) -> float:
    ct = {t.strip().lower() for t in chunk_tags.split(',') if t.strip()}
    if not ct or not query_tags:
        return 0.0
    qt = {t.lower() for t in query_tags}
    overlap = len(ct & qt)
    return overlap / max(len(ct), 1) if overlap else 0.0


def retrieve(state: dict, top_k: int = 3) -> list:
    init_knowledge_base()
    query_tags = _tags_from_state(state)
    if not query_tags:
        return []

    conn = _conn()
    rows = conn.execute(
        "SELECT id, tags, content, outcome, source, weight FROM knowledge_chunks"
    ).fetchall()
    conn.close()

    scored = []
    for rid, tags, content, outcome, source, weight in rows:
        s = _score_chunk(tags, query_tags) * float(weight or 1)
        if s > 0:
            scored.append({
                'id': rid, 'score': s, 'tags': tags, 'content': content,
                'outcome': outcome, 'source': source,
            })
    scored.sort(key=lambda x: -x['score'])
    return scored[:top_k]


def record_rag_usage(chunks: list):
    """Increment use counter when knowledge is applied to a trade/shadow."""
    if not chunks:
        return
    init_knowledge_base()
    conn = _conn()
    for c in chunks:
        cid = c.get('id')
        if cid:
            conn.execute(
                "UPDATE knowledge_chunks SET uses = uses + 1 WHERE id=?", (cid,)
            )
    conn.commit()
    conn.close()


def get_rag_usage_stats() -> dict:
    init_knowledge_base()
    conn = _conn()
    total_uses = conn.execute(
        "SELECT COALESCE(SUM(uses), 0) FROM knowledge_chunks"
    ).fetchone()[0]
    trade_uses = conn.execute(
        "SELECT COALESCE(SUM(uses), 0) FROM knowledge_chunks WHERE source='trade'"
    ).fetchone()[0]
    top = conn.execute(
        "SELECT content, uses, outcome FROM knowledge_chunks "
        "WHERE uses > 0 ORDER BY uses DESC LIMIT 3"
    ).fetchall()
    conn.close()
    return {'total_uses': total_uses, 'trade_uses': trade_uses, 'top': top}


def ingest_trade_lesson(
    session: str, bias: str, regime: str,
    mistake: str, lesson: str, outcome: str, cpr_class: str = '',
    weight: float = None,
):
    if not lesson:
        return
    init_knowledge_base()
    tags = [session.lower(), bias.lower(), regime.lower(), outcome.lower()]
    if mistake and mistake != 'NONE':
        tags.append(f"mistake:{mistake}")
    if cpr_class:
        tags.extend(['cpr', cpr_class.lower()])
    tag_str = ','.join(t for t in tags if t)

    if weight is None:
        weight = 1.2 if outcome == 'WIN' else 1.0
        if str(mistake).startswith('SHADOW'):
            try:
                from src.shadow_learning import is_learning_phase
                if is_learning_phase():
                    weight = 2.0
            except Exception:
                pass

    conn = _conn()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    dup = conn.execute(
        "SELECT id FROM knowledge_chunks WHERE content=? AND created_at LIKE ?",
        (lesson[:200], f"{today}%"),
    ).fetchone()
    if not dup:
        conn.execute(
            "INSERT INTO knowledge_chunks (tags, content, outcome, source, weight, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (tag_str, lesson[:500], outcome, 'trade', weight,
             datetime.now(IST).strftime('%Y-%m-%d %H:%M')),
        )
        conn.commit()
    conn.close()


def apply_rag_to_signal(signal: dict) -> dict:
    from core.shared_state import STATE
    ctx = STATE.get('market.context') or {}
    state = {
        'session': signal.get('session', ''),
        'regime':  signal.get('regime', ''),
        'trend':   signal.get('trend', ''),
        'context': ctx,
    }
    chunks = retrieve(state, top_k=4)
    if not chunks:
        return {'ok': True, 'score_delta': 0, 'reasons': [], 'lessons': []}

    record_rag_usage(chunks)

    reasons = []
    score_delta = 0
    for c in chunks:
        reasons.append(f"📚 {c['content'][:120]}")
        if c['outcome'] == 'WIN':
            score_delta += 1
        elif c['outcome'] == 'LOSS':
            score_delta -= 1
        if c['outcome'] == 'LOSS' and c['score'] >= 0.6 and 'mistake' in c['tags']:
            return {
                'ok': False,
                'reason': f"🧠 RAG memory: {c['content'][:180]}",
                'score_delta': 0, 'reasons': reasons, 'lessons': chunks,
            }

    return {
        'ok': True,
        'score_delta': max(-2, min(2, score_delta)),
        'reasons': reasons[:3], 'lessons': chunks,
    }


def format_learn_report() -> str:
    init_knowledge_base()
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    seeds = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE source='seed'"
    ).fetchone()[0]
    trades = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE source='trade'"
    ).fetchone()[0]
    recent = conn.execute(
        "SELECT content, outcome, source FROM knowledge_chunks ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()

    from src.market_rag import get_rag_usage_stats
    usage = get_rag_usage_stats()

    lines = [
        "🧠 *Market RAG — Bot Memory*",
        "━━━━━━━━━━━━━━━━━━━",
        f"Total chunks: {total} ({seeds} rules + {trades} from your trades)",
        f"Knowledge *reused* {usage['total_uses']} times on setups",
        "",
        "*How it works:* every setup retrieves matching rules → adjusts score",
        "*Shadow + paper exits* add new lessons automatically",
        "",
    ]
    if usage['top']:
        lines.append("*Most used rules:*")
        for content, uses, outcome in usage['top']:
            lines.append(f"  ↻ {uses}× [{outcome}] {content[:65]}...")
        lines.append("")
    lines += [
        "*Recent knowledge:*",
    ]
    for content, outcome, source in recent:
        icon = '📗' if outcome == 'WIN' else '📕' if outcome == 'LOSS' else '📘'
        lines.append(f"  {icon} [{source}] {content[:70]}...")
    lines.append("\n_Use /cpr for today's Central Pivot Range_")
    return '\n'.join(lines)
