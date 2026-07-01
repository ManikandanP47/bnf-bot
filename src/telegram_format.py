"""Telegram legacy Markdown helpers — underscores in ENV_VARS break italic parsing."""

import re

# ALL_CAPS identifiers (PAPER_MODE, GROWW_HIST, …)
_ENV_IDENT_RE = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b')
# lowercase API keys (groww_ltp, nse_oi, market_flow) — _ opens stray italics
_SNAKE_IDENT_RE = re.compile(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b')


def sanitize_telegram_markdown(text: str) -> str:
    """Replace env/API underscores so Telegram Markdown does not open stray italics."""
    text = _ENV_IDENT_RE.sub(lambda m: m.group(0).replace('_', '-'), text)
    return _SNAKE_IDENT_RE.sub(lambda m: m.group(0).replace('_', '-'), text)
