"""Telegram legacy Markdown helpers — underscores in ENV_VARS break italic parsing."""

import re

# ALL_CAPS identifiers with underscores (PAPER_MODE, GROWW_HIST, …)
_ENV_IDENT_RE = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b')


def sanitize_telegram_markdown(text: str) -> str:
    """Replace env-style underscores so Telegram Markdown does not open stray italics."""
    return _ENV_IDENT_RE.sub(lambda m: m.group(0).replace('_', '-'), text)
