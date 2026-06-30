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
POLL_TIMEOUT = 20  # Telegram long-poll seconds


def _display_source(source: str) -> str:
    """Avoid Telegram Markdown breaking on underscores (e.g. GROWW_HIST)."""
    return str(source or 'N/A').replace('_', '-')


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
                params={'offset': self.offset, 'timeout': POLL_TIMEOUT,
                        'allowed_updates': ['message', 'callback_query']},
                timeout=POLL_TIMEOUT + 10
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
        try:
            from src.shadow_learning import paper_trading_allowed, learning_phase_info
            if not paper_trading_allowed():
                info = learning_phase_info()
                return (
                    f"🎓 *Week 1–2: virtual sim only*\n\n"
                    f"Paper trading unlocks in *{info['days_until_paper']}* day(s).\n"
                    f"Bot is learning from live-market sims — `/shadow` `/ml`"
                )
        except Exception:
            pass
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
            source       = _display_source(STATE.get('market.data_source', 'N/A'))
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
            live_reason  = STATE.get(
                'system.live_readiness_summary',
                'Paper mode — send /readiness for full checklist',
            )
            brain    = STATE.get('brain', {})
            backup_line = ''
            shadow_line = ''
            try:
                from src.ops_backup import format_backup_status
                backup_line = f"\n*Backup:* {format_backup_status()}"
            except Exception:
                pass
            try:
                from src.brain_metrics import get_dynamic_min_score
                from src.shadow_tuning import shadow_score_adjustment
                base = brain.get('min_score', 5)
                dyn = get_dynamic_min_score(base)
                sh = shadow_score_adjustment(base)
                if sh.get('reason'):
                    shadow_line = f"\n*Shadow tune:* min {dyn} — {sh['reason'][:60]}"
                elif dyn > base:
                    shadow_line = f"\n*Min score:* {dyn} (raised from {base})"
            except Exception:
                pass
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
                f"*Brain:* {brain.get('learning_stage', 'EARLY')}{shadow_line}{backup_line}\n\n"
                f"*Live readiness:* {live_reason}"
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

        elif cmd == '/groww':
            from src.groww_health import format_groww_health
            return format_groww_health()

        elif cmd == '/why':
            from src.funnel_why import format_why_report
            return format_why_report()

        elif cmd == '/funnel':
            from src.trade_analytics import format_funnel_report
            return format_funnel_report()

        elif cmd == '/context':
            from src.market_context import format_context_report
            return format_context_report()

        elif cmd == '/backtest':
            from src.history_backtest import format_backtest_report
            return format_backtest_report()

        elif cmd == '/cpr':
            from src.cpr import format_cpr_report
            ctx = STATE.get('market.context') or {}
            from src.market_context import build_market_context
            if not ctx.get('cpr'):
                ctx = build_market_context()
            return format_cpr_report(ctx)

        elif cmd == '/learn':
            from src.market_rag import format_learn_report
            from src.shadow_learning import format_shadow_brief
            from src.shadow_tuning import shadow_score_adjustment
            tune = shadow_score_adjustment(5)
            tune_line = f"\n\n{tune['reason']}" if tune.get('reason') else ''
            return format_learn_report() + "\n\n" + format_shadow_brief() + tune_line

        elif cmd == '/ml':
            from src.ml_brain import format_ml_status
            return format_ml_status()

        elif cmd == '/resetlearning':
            from src.sim_learning_report import reset_graduation_flag, format_reset_learning_help
            reset_graduation_flag()
            return format_reset_learning_help()

        elif cmd == '/simreport':
            from src.sim_learning_report import (
                format_daily_sim_training_report, format_graduation_report,
            )
            from src.shadow_learning import learning_phase_info
            info = learning_phase_info()
            if info['phase'] in ('SIM', 'PAPER'):
                return format_daily_sim_training_report()
            return format_graduation_report()

        elif cmd == '/shadow':
            from src.shadow_learning import (
                format_shadow_daily_section, learning_phase_info, format_auto_learning_status,
            )
            from src.market_simulator import format_sim_status
            info = learning_phase_info()
            phase_labels = {
                'SIM': 'Week 1–2 · virtual sim only',
                'PAPER': 'Week 3–4 · paper /execute',
                'LIVE_READY': 'Month done · /readiness for live',
            }
            hdr = (
                f"🎓 *Training plan*\n"
                f"Phase: *{info['phase']}* — {phase_labels.get(info['phase'], '')}\n"
                f"Paper unlocks in: {info['days_until_paper']}d | "
                f"Live window in: {info['days_until_live']}d\n\n"
                f"{format_auto_learning_status()}\n"
                f"{format_sim_status()}\n"
            )
            return hdr + format_shadow_daily_section()

        elif cmd == '/today':
            from src.today_dashboard import format_today_dashboard
            return format_today_dashboard()

        elif cmd == '/flow':
            from src.market_flow import refresh_market_flow, format_flow_report
            zone = STATE.get('zone') or {}
            refresh_market_flow(zone.get('bias', 'BULLISH'))
            return format_flow_report()

        elif cmd == '/help':
            from src.telegram_help import format_full_help
            return format_full_help()

        return f"❓ Unknown: `{cmd}`\nType /help for commands."

    def _process_command(self, text: str):
        try:
            reply = self._handle(text)
            if not self.messenger.send(reply):
                print(f"⚠️  Telegram did not accept reply for {text}")
        except Exception as e:
            print(f"⚠️  Command handler error ({text}): {e}")
            self.messenger.send(
                f"Command failed: {str(e)[:180]}\nTry /help",
                parse_mode=None,
            )

    def run(self):
        print("📱 Command Listener started")
        STATE.set_agent_status('telegram', 'RUNNING')
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
                    try:
                        from src.telegram_mirror import mirror_message
                        mirror_message('in', text, kind='command')
                    except Exception:
                        pass
                    threading.Thread(
                        target=self._process_command,
                        args=(text,),
                        daemon=True,
                        name=f'cmd-{text[1:8]}',
                    ).start()
            except Exception as e:
                print(f"⚠️  Command listener error: {e}")
            sleep_secs = 1 if STATE.get('signals.confirmation_sent') else 2
            time.sleep(sleep_secs)
