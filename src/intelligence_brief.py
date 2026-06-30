"""
Live intelligence brief — fuses RAG, patterns, backtest, ML, market context.
Powers dashboard + future trade scoring enhancements.
"""

from core.shared_state import STATE


def build_intelligence_brief() -> dict:
    """Unified live intelligence for dashboard and analysis hints."""
    price = STATE.get('market.price', 0)
    session = STATE.get('market.session', '')
    zone = STATE.get('zone', {}) or {}
    bias = zone.get('bias', 'NEUTRAL') if zone.get('active') else 'NEUTRAL'

    signal = {
        'price': price,
        'trend': bias,
        'session': session,
        'regime': STATE.get('market.regime', 'TRENDING'),
        'rsi': STATE.get('market.rsi_5m', 50),
        'score': 7,
        'context': STATE.get('market.context') or {},
    }

    rag_chunks = []
    rag_usage = {}
    try:
        from src.market_rag import retrieve, get_usage_stats
        rag_chunks = retrieve(signal, top_k=5)
        rag_usage = get_usage_stats()
    except Exception:
        pass

    patterns = []
    try:
        from src.db_persistence import connect
        conn = connect()
        rows = conn.execute("""
            SELECT pattern_key, wins, losses, samples, total_pnl
            FROM pattern_memory
            ORDER BY samples DESC LIMIT 6
        """).fetchall()
        conn.close()
        for key, w, l, n, pnl in rows:
            wr = round(w / (w + l) * 100, 1) if (w + l) else 0
            patterns.append({
                'key': key.replace('shadow:', ''),
                'wr': wr, 'samples': n, 'pnl': pnl,
            })
    except Exception:
        pass

    ml_prob = None
    try:
        from src.ml_brain import predict_win_probability
        ml_prob = predict_win_probability(signal)
    except Exception:
        pass

    backtest = STATE.get('system.backtest_summary') or {}
    ctx = STATE.get('market.context') or {}

    suggestions = _build_suggestions(
        session, zone, rag_chunks, patterns, ml_prob, backtest
    )

    return {
        'bias': bias,
        'session': session,
        'rag_chunks': [
            {'content': c['content'][:200], 'score': round(c['score'], 2),
             'source': c.get('source', '')}
            for c in rag_chunks
        ],
        'rag_usage': rag_usage,
        'patterns': patterns,
        'ml': ml_prob,
        'backtest': {
            'proxy_wr': backtest.get('proxy_win_rate', 0),
            'proxy_trades': backtest.get('proxy_trades', 0),
            'note': backtest.get('note', ''),
        },
        'context': {
            'cpr': (ctx.get('cpr') or {}).get('width_class', ''),
            'cpr_position': (ctx.get('cpr_position') or {}).get('zone', ''),
            'vix_regime': (ctx.get('vix') or {}).get('regime', ''),
        },
        'suggestions': suggestions,
        'roadmap': _intelligence_roadmap(),
    }


def _build_suggestions(session, zone, rag, patterns, ml, backtest) -> list:
    out = []
    if session in ('LUNCH_CHOP', 'EOD_CHOP', 'OPEN_VOLATILE'):
        out.append(
            f'Session {session}: sim relaxed; execute path stays strict.'
        )
    if not zone.get('active'):
        out.append('No zone — evening scan ~8:15 PM sets tomorrow plan.')
    elif zone.get('active') and not zone.get('used'):
        low, high = zone.get('low', 0), zone.get('high', 0)
        out.append(
            f"Zone {zone.get('bias')} active — pullback watch "
            f"{low:,.0f}–{high:,.0f}"
        )
    if ml and ml.get('ready'):
        out.append(
            f"ML: {ml.get('prob_pct')}% win est. (CV {ml.get('cv_accuracy')}%)"
        )
    elif ml and not ml.get('ready'):
        out.append(f"ML warming: {ml.get('reason', 'need more samples')}")
    if backtest.get('proxy_trades', 0) >= 3:
        out.append(
            f"10d proxy: {backtest.get('proxy_win_rate', 0)}% WR "
            f"({backtest.get('proxy_trades')} mornings)"
        )
    strong = [p for p in patterns if p.get('samples', 0) >= 5 and p.get('wr', 0) >= 55]
    if strong:
        out.append(f"Pattern: {strong[0]['key']} ({strong[0]['wr']}% WR)")
    if rag:
        out.append(f"RAG: {rag[0]['content'][:90]}…")
    return out[:6]


def _intelligence_roadmap() -> list:
    return [
        {'id': 'greeks', 'status': 'active',
         'title': 'Greeks + NSE IV',
         'detail': 'Delta/theta/vega from chain IV; ML features at trade close'},
        {'id': 'embeddings', 'status': 'planned',
         'title': 'Embedding RAG',
         'detail': 'Semantic lesson search after 50+ closes'},
        {'id': 'regime', 'status': 'active',
         'title': 'Regime + session gates',
         'detail': 'LUNCH_CHOP / EOD filtered on execute'},
        {'id': 'ensemble', 'status': 'active',
         'title': 'RF + NN ensemble',
         'detail': 'Auto at 25 / 100 labeled closes'},
        {'id': 'backtest_rag', 'status': 'active',
         'title': 'Backtest → RAG',
         'detail': 'Groww proxy WR in knowledge base'},
        {'id': 'sim_gap', 'status': 'active',
         'title': 'Sim vs Execute gap',
         'detail': 'Dashboard shows strict-path blocks'},
    ]
