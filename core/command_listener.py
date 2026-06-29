"""
Telegram Command Listener
Polls Telegram for commands from Mani every 30 seconds.

Supported commands:
  /pause   → stop entries today
  /resume  → resume normal trading
  /status  → show all agents + position status
  /pnl     → today's P&L summary
  /zone    → tonight's saved zone details
  /execute → confirm pending trade suggestion
  /skip    → skip pending trade suggestion
  /stop    → emergency stop all trading
  /help    → show all commands
"""

import os, time, threading, requests
from datetime import datetime
import pytz
from core.shared_state import STATE

IST = pytz.timezone('Asia/Kolkata')


class CommandListener(threading.Thread):

    def __init__(self, messenger):
        super().__init__(daemon=True, name='CommandListener')
        self.messenger = messenger
        self.token     = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id   = str(os.getenv('TELEGRAM_CHAT_ID', ''))
        self.offset    = 0

    def _get_updates(self) -> list:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={'offset': self.offset, 'timeout': 10,
                        'allowed_updates': ['message', 'callback_query']},
                timeout=20
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('ok'):
                    return data.get('result', [])
        except Exception:
            pass
        return []

    def _answer_callback(self, callback_id: str):
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
                json={'callback_query_id': callback_id},
                timeout=10
            )
        except Exception:
            pass

    def _confirm_trade(self) -> str:
        if not STATE.get('signals.confirmation_sent'):
            return "❌ No pending trade suggestion. Bot is watching for setups."
        if STATE.get('position.open'):
            return "❌ Already in a position."
        STATE.set('signals.awaiting_confirmation', False)
        STATE.set('signals.execute_now', True)
        paper = os.getenv('PAPER_MODE', 'true').lower() == 'true'
        mode  = "paper" if paper else "live"
        return f"✅ *Confirmed — executing {mode} trade...*\nMonitor Agent will track SL/Target."

    def _skip_trade(self) -> str:
        if not STATE.get('signals.confirmation_sent'):
            return "❌ No pending trade to skip."
        signal = STATE.get('signals.analysis') or {}
        params = STATE.get('signals.pending_params') or {}
        if signal and params:
            from src.trade_analytics import log_skip
            log_skip(signal, params)
        STATE.update('signals', {
            'awaiting_confirmation': False,
            'confirmation_sent':     False,
            'execute_now':           False,
            'risk_approved':         False,
            'analysis':              None,
            'risk':                  None,
            'pending_params':        None,
        })
        return (
            "⏭ *Trade skipped*\n"
            "Logged for skip-learning — bot checks at EOD if skip was right.\n"
            "_Watching for next setup_"
        )

    def _handle(self, text: str) -> str:
        cmd = text.strip().lower().split()[0]

        if cmd == '/pause':
            STATE.set('system.paused', True)
            now = datetime.now(IST).strftime('%I:%M %p')
            return (
                f"⏸️ *Bot Paused at {now}*\n\n"
                f"No new entries today.\n"
                f"Open positions still monitored ✅\n"
                f"Type /resume to restart."
            )

        elif cmd == '/resume':
            STATE.set('system.paused', False)
            return (
                f"▶️ *Bot Resumed*\n\n"
                f"Watching for setups again ✅"
            )

        elif cmd == '/stop':
            STATE.set('system.paused', True)
            STATE.set('zone.active', False)
            return (
                f"🛑 *Emergency Stop*\n\n"
                f"All entries blocked.\n"
                f"Open position monitored for EOD exit.\n"
                f"Type /resume tomorrow to restart."
            )

        elif cmd == '/status':
            now          = datetime.now(IST).strftime('%d %b %Y %I:%M %p')
            paused       = STATE.get('system.paused', False)
            mkt_open     = STATE.get('system.market_open', False)
            price        = STATE.get('market.price', 0)
            session      = STATE.get('market.session', 'CLOSED')
            source       = STATE.get('market.data_source', 'N/A')
            pos_open     = STATE.get('position.open', False)
            pos_name     = STATE.get('position.name', '—')
            trades_today = STATE.get('brain.trades_today', 0)
            today_pnl    = STATE.get('brain.today_pnl', 0)
            zone_active  = STATE.get('zone.active', False)
            zone_low     = STATE.get('zone.low', 0)
            zone_high    = STATE.get('zone.high', 0)
            weekly_loss  = STATE.get('system.weekly_losses', 0)
            pnl_emoji    = '🟢' if today_pnl >= 0 else '🔴'
            agents       = STATE.get('system.agent_status', {})
            agent_lines  = '\n'.join(
                f"  {k.capitalize()}: {v}" for k, v in agents.items()
            )
            from src.capital_guard import assess_live_readiness
            live_chk = assess_live_readiness()
            brain    = STATE.get('brain', {})
            return (
                f"📊 *Bot Status — {now}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"Mode: {'⏸️ PAUSED' if paused else '▶️ ACTIVE'}\n"
                f"Market: {'🟢 Open' if mkt_open else '🔴 Closed'} | {session}\n"
                f"BNF: {price:,.0f} ({source})\n\n"
                f"*Agents:*\n{agent_lines}\n\n"
                f"*Position:* {'📌 ' + pos_name if pos_open else 'None'}\n"
                f"*Zone:* {'✅ ' + f'{zone_low:,.0f}–{zone_high:,.0f}' if zone_active else 'None'}\n\n"
                f"*Today:* {trades_today} trade(s) | {pnl_emoji} ₹{today_pnl:,.0f}\n"
                f"*Week losses:* {weekly_loss}/2\n"
                f"*Brain:* {brain.get('learning_stage', 'EARLY')}\n\n"
                f"*Live readiness:* {live_chk['reason']}"
            )

        elif cmd == '/pnl':
            today_pnl    = STATE.get('brain.today_pnl', 0)
            trades_today = STATE.get('brain.trades_today', 0)
            weekly_loss  = STATE.get('system.weekly_losses', 0)
            pos_open     = STATE.get('position.open', False)
            pos_name     = STATE.get('position.name', '')
            entry        = STATE.get('position.entry_price', 0)
            trail_sl     = STATE.get('position.trail_sl', 0)
            tgt_prem     = STATE.get('position.tgt_prem', 0)
            pnl_emoji    = '🟢' if today_pnl >= 0 else '🔴'
            live = (
                f"\n*Live Position:*\n"
                f"  {pos_name}\n"
                f"  Entry ₹{entry} | SL ₹{trail_sl} | Target ₹{tgt_prem}"
            ) if pos_open else ''
            return (
                f"{pnl_emoji} *P&L Summary*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"Today: ₹{today_pnl:,.0f}\n"
                f"Trades today: {trades_today}\n"
                f"Losses this week: {weekly_loss}/2"
                f"{live}"
            )

        elif cmd == '/zone':
            zone = STATE.get('zone', {})
            if not zone or not zone.get('active'):
                return (
                    "📌 *No active zone*\n\n"
                    "Evening scan runs at 8:15 PM.\n"
                    "Check back after that."
                )
            bias  = zone.get('bias', '?')
            emoji = '🟢' if bias == 'BULLISH' else '🔴'
            stars = '⭐' * min(zone.get('score', 0), 5)
            return (
                f"📌 *Active Zone*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{emoji} {bias} | {stars}\n\n"
                f"Zone: {zone.get('low', 0):,.0f}–{zone.get('high', 0):,.0f}\n"
                f"Option: *{zone.get('option_name', '?')}*\n"
                f"Premium: ~₹{zone.get('premium', 0)}\n"
                f"SL: ₹{zone.get('sl_prem', 0)} | Target: ₹{zone.get('tgt_prem', 0)}\n"
                f"Saved: {zone.get('saved_at', '?')}\n"
                f"Used: {'Yes' if zone.get('used') else 'No — watching for entry'}"
            )

        elif cmd == '/execute':
            return self._confirm_trade()

        elif cmd == '/skip':
            return self._skip_trade()

        elif cmd == '/journal':
            from src.paper_journal import format_journal_command
            return format_journal_command()

        elif cmd == '/readiness':
            from src.brain_metrics import format_readiness_report
            return format_readiness_report()

        elif cmd == '/funnel':
            from src.trade_analytics import format_funnel_report
            return format_funnel_report()

        elif cmd == '/help':
            return (
                "🤖 *BNF Bot Commands*\n"
                "━━━━━━━━━━━━━━━━━\n"
                "/pause   — Stop entries today\n"
                "/resume  — Resume trading\n"
                "/execute — Confirm pending trade\n"
                "/skip    — Skip pending trade\n"
                "/journal — Today's paper trades + brain\n"
                "/readiness — Live gate checklist (8 gates)\n"
                "/funnel — Signal funnel + skip learning\n"
                "/stop    — Emergency stop\n"
                "/status  — All agents + position\n"
                "/pnl     — Today's P&L\n"
                "/zone    — Tonight's zone\n"
                "/help    — This message\n\n"
                "_Paper first — all gates green before live ₹5k_ 🛡️"
            )

        return f"❓ Unknown: `{cmd}`\nType /help for commands."

    def run(self):
        print("📱 Command Listener started")
        while STATE.get('system.running'):
            try:
                updates = self._get_updates()
                for update in updates:
                    self.offset = update['update_id'] + 1

                    # Inline button taps
                    if 'callback_query' in update:
                        cq = update['callback_query']
                        chat_id = str(
                            cq.get('message', {}).get('chat', {}).get('id', '')
                        )
                        if chat_id != self.chat_id:
                            continue
                        data = cq.get('data', '')
                        self._answer_callback(cq.get('id', ''))
                        if data == 'trade_execute':
                            print("📱 Callback: trade_execute")
                            self.messenger.send(self._confirm_trade())
                        elif data == 'trade_skip':
                            print("📱 Callback: trade_skip")
                            self.messenger.send(self._skip_trade())
                        continue

                    msg = update.get('message', {})
                    if not msg:
                        continue
                    from_id = str(msg.get('chat', {}).get('id', ''))
                    if from_id != self.chat_id:
                        continue
                    text = msg.get('text', '').strip()
                    if not text.startswith('/'):
                        continue
                    print(f"📱 Command: {text}")
                    self.messenger.send(self._handle(text))
            except Exception:
                pass
            sleep_secs = 5 if STATE.get('signals.confirmation_sent') else 30
            time.sleep(sleep_secs)
