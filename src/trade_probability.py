"""Win probability estimate from pattern_memory + shadow stats."""

import os


def estimate_win_probability(signal: dict) -> dict:
    """
    Bayesian-style blend of pattern keys for this setup.
    Returns {prob_pct, label, detail}.
    """
    from agents.learning_agent import BRAIN

    session = signal.get('session', '')
    score = signal.get('score', 0)
    regime = signal.get('regime', '')
    hour = signal.get('hour')
    if hour is None:
        from datetime import datetime
        import pytz
        hour = datetime.now(pytz.timezone('Asia/Kolkata')).hour

    keys = [
        f"hour:{hour}|session:{session}",
        f"score:{score}|regime:{regime}",
        f"session:{session}",
        f"regime:{regime}",
    ]

    samples, weighted_wr = 0, 0.0
    details = []
    for k in keys:
        wr = BRAIN.get_pattern_winrate(k, min_samples=3)
        if wr is None:
            continue
        n = BRAIN.conn.execute(
            "SELECT samples FROM pattern_memory WHERE pattern_key=?", (k,)
        ).fetchone()
        n = n[0] if n else 3
        weighted_wr += wr * n
        samples += n
        details.append(f'{k}: {wr:.0f}%')

    shadow_wr = 0.0
    shadow_n = 0
    try:
        from src.learning_scoreboard import shadow_vs_paper_stats
        s = shadow_vs_paper_stats(14)
        shadow_wr = s.get('shadow_wr', 0)
        shadow_n = s.get('shadow_n', 0)
    except Exception:
        pass

    if samples >= 5:
        prob = weighted_wr / samples
    elif shadow_n >= 8:
        prob = shadow_wr * 0.7 + 50 * 0.3
        details.append(f'shadow 14d: {shadow_wr}%')
    else:
        prob = 45 + min(score, 10) * 2
        details.append('prior: score-based')

    prob = max(20, min(85, round(prob, 1)))

    if prob >= 58:
        label = 'STRONG'
    elif prob >= 48:
        label = 'FAIR'
    else:
        label = 'WEAK'

    return {
        'prob_pct': prob,
        'label': label,
        'detail': '; '.join(details[:3]),
    }


def format_probability_line(signal: dict) -> str:
    p = estimate_win_probability(signal)
    return (
        f"🎯 *Win estimate:* {p['prob_pct']}% ({p['label']})"
        + (f"\n  _{p['detail']}_" if p.get('detail') else '')
    )
