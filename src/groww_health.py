"""Groww API health — /groww command and auth-degraded alerts."""

import os
import json
import time
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
CACHE_FILE = '.groww_token_cache.json'
RATE_FILE = '.groww_rate_limit.json'


def _cache_age_sec() -> int:
    try:
        if not os.path.exists(CACHE_FILE):
            return -1
        with open(CACHE_FILE) as f:
            ts = json.load(f).get('ts', 0)
        return int(time.time() - ts)
    except Exception:
        return -1


def format_groww_health() -> str:
    from core.shared_state import STATE
    from src.groww_auth import is_rate_limited, rate_limit_remaining_sec

    src = _display_source(STATE.get('market.data_source', 'N/A'))
    connected = STATE.get('market.connected', False)
    price = STATE.get('market.price', 0)
    token = STATE.get('system.groww_token', '')
    cache_age = _cache_age_sec()
    cache_line = (
        f'{cache_age // 60}m old' if cache_age >= 0 else 'none'
    )

    lines = [
        '🔌 *Groww API Health*',
        '━━━━━━━━━━━━━━━━━━━',
        f"Price: {price:,.0f} ({src})" if price else f"Price: — ({src})",
        f"Connected: {'✅' if connected else '❌'}",
        f"Token in memory: {'✅' if token.startswith('eyJ') else '❌'}",
        f"Token cache: {cache_line}",
    ]

    if is_rate_limited():
        lines.append(f"⏸️ TOTP cooldown: {rate_limit_remaining_sec() // 60}m left")
    else:
        lines.append('✅ TOTP cooldown: clear')

    try:
        from src.groww_feed_store import format_feed_status_line
        lines.append('')
        lines.append(format_feed_status_line())
    except Exception:
        pass

    try:
        from src.api_scheduler import format_scheduler_status
        lines.append('')
        lines.append(format_scheduler_status())
    except Exception:
        pass

    lines += [
        '',
        '*Tip:* never run Groww tests on Mac while server bot is live.',
    ]
    return '\n'.join(lines)


def _display_source(source: str) -> str:
    return str(source or 'N/A').replace('_', '-')


def maybe_alert_auth_degraded(messenger, last_alert_day: int) -> int:
    """Once per day if price is on yfinance fallback during live market hours."""
    from datetime import time as dtime
    from core.shared_state import STATE
    from src.safety import check_trading_day

    now = datetime.now(IST)
    if not check_trading_day().get('trade'):
        return last_alert_day
    # NSE cash opens 9:15 — no alert during pre-open warm-up (9:00 yfinance is normal)
    if not STATE.get('system.market_open'):
        return last_alert_day
    if now.time() < dtime(9, 20):
        return last_alert_day
    if not (9 <= now.hour <= 15):
        return last_alert_day

    try:
        from src.groww_feed_store import is_feed_live
        if is_feed_live():
            return last_alert_day
    except Exception:
        pass

    src = str(STATE.get('market.data_source', '')).upper()
    if 'YFINANCE' not in src:
        return last_alert_day
    if last_alert_day == now.day:
        return last_alert_day

    # One refresh attempt before alerting — overnight cache often expires by open
    try:
        from src.groww_auth import fetch_groww_token, is_rate_limited
        if not is_rate_limited():
            fetch_groww_token(force_refresh=True, max_retries=1, base_delay_sec=0)
            src2 = str(STATE.get('market.data_source', '')).upper()
            if 'YFINANCE' not in src2:
                return last_alert_day
    except Exception:
        pass

    from src.groww_auth import is_rate_limited, rate_limit_remaining_sec
    mins = rate_limit_remaining_sec() // 60
    reason = f'cooldown {mins}m' if is_rate_limited() else 'auth failed'
    messenger.send(
        f"⚠️ *Groww degraded* ({reason})\n\n"
        f"Using yfinance for BNF price — analysis continues.\n"
        f"Sim training still runs; live execute may use stale premium.\n"
        f"Send /groww for status."
    )
    return now.day
