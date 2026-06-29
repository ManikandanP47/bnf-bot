"""
OpenAI advisor — short trade/journal summaries (gpt-4o-mini).
Set OPENAI_API_KEY in .env — never commit the key.
"""

import os
import json
import urllib.request
import urllib.error

DEFAULT_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
MAX_TOKENS = int(os.getenv('OPENAI_MAX_TOKENS', '280'))


def llm_enabled() -> bool:
    return bool(os.getenv('OPENAI_API_KEY', '').strip())


def llm_chat(system: str, user: str, max_tokens: int = None) -> str:
    """Call OpenAI chat completions API. Returns '' on failure."""
    key = os.getenv('OPENAI_API_KEY', '').strip()
    if not key:
        return ''
    payload = {
        'model': DEFAULT_MODEL,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user[:4000]},
        ],
        'max_tokens': max_tokens or MAX_TOKENS,
        'temperature': 0.4,
    }
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        return (data['choices'][0]['message']['content'] or '').strip()
    except Exception:
        return ''


def explain_trade_setup(signal: dict, risk: dict = None) -> str:
    """One paragraph for trade suggestion card."""
    if not llm_enabled():
        return ''
    user = (
        f"BankNifty salary trader ₹5000, paper mode.\n"
        f"Signal: {signal.get('trend')} score {signal.get('score')} "
        f"session {signal.get('session')} price {signal.get('price')}\n"
        f"Reasons: {signal.get('reasons', [])[:5]}\n"
        f"Risk notes: {(risk or {}).get('warnings', [])[:3]}\n"
        "In 3-4 short bullets: edge, main risk, hold plan. Plain English, under 120 words."
    )
    return llm_chat(
        "You are a concise Indian F&O trading coach for a small retail account.",
        user,
        max_tokens=200,
    )


def summarize_day(context: str) -> str:
    """EOD or /today AI summary."""
    if not llm_enabled():
        return ''
    return llm_chat(
        "Summarize this trading bot day for a salary trader. "
        "Be honest: what worked, what failed, one lesson. Max 100 words.",
        context,
        max_tokens=220,
    )


def weekly_coach_note(stats_block: str) -> str:
    if not llm_enabled():
        return ''
    return llm_chat(
        "Weekly coach for BankNifty option buyer with ₹5k capital. "
        "Compare shadow vs paper if given. One focus for next week.",
        stats_block,
        max_tokens=250,
    )
