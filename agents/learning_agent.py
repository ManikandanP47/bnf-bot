"""
Learning Agent — The Trader Brain (Research-Grade)

Based on research findings:
- SQLite with WAL mode for concurrent reads
- Minimum 30 trades before threshold adjustment (prevents overfitting)
- Fractional Kelly position sizing (0.25x multiplier)
- Mistake clustering for loss classification
- Pattern confidence with sample-size guard
"""

import threading, json, os, time, sqlite3, random
from datetime import datetime, timedelta
from collections import defaultdict
import pytz

from core.shared_state import STATE
from core.messenger     import Messenger

IST     = pytz.timezone('Asia/Kolkata')
DB_FILE = os.getenv('DB_PATH', 'trader_brain.db')

MIN_TRADES_TO_LEARN  = 30   # Research: don't adjust on <30 trades
KELLY_FRACTION       = 0.25  # Conservative quarter-Kelly


class TraderBrain:
    """
    SQLite brain with WAL mode for concurrent reads.
    Learns from every trade. Adapts thresholds only when
    statistically meaningful (min 30 trades per pattern).
    """

    def __init__(self):
        self.conn = sqlite3.connect(
            DB_FILE, check_same_thread=False,
            isolation_level=None  # autocommit for WAL
        )
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT,
                entry_time   TEXT,
                exit_time    TEXT,
                option_name  TEXT,
                bias         TEXT,
                session      TEXT,
                hour         INTEGER,
                day_of_week  TEXT,
                bnf_at_entry REAL,
                bnf_range    TEXT,
                score        INTEGER,
                regime       TEXT,
                rsi          REAL,
                volume_ratio REAL,
                vwap_vs_price TEXT,
                entry_prem   REAL,
                exit_prem    REAL,
                sl_prem      REAL,
                tgt_prem     REAL,
                pnl_rs       REAL,
                pnl_pct      REAL,
                r_multiple   REAL,
                outcome      TEXT,
                exit_reason  TEXT,
                hold_minutes INTEGER,
                mistake_type TEXT,
                lesson       TEXT,
                followed_rules INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS pattern_memory (
                pattern_key TEXT PRIMARY KEY,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0,
                total_pnl   REAL    DEFAULT 0,
                samples     INTEGER DEFAULT 0,
                last_seen   TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_pnl (
                date      TEXT PRIMARY KEY,
                trades    INTEGER,
                wins      INTEGER,
                pnl       REAL,
                max_loss  REAL,
                notes     TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_funnel (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                date    TEXT,
                time    TEXT,
                stage   TEXT,
                score   INTEGER,
                bias    TEXT,
                session TEXT,
                reason  TEXT
            );
            CREATE TABLE IF NOT EXISTS skipped_setups (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT,
                time          TEXT,
                bias          TEXT,
                score         INTEGER,
                price         REAL,
                option_name   TEXT,
                entry_prem    REAL,
                sl_prem       REAL,
                tgt_prem      REAL,
                session       TEXT,
                signal_json   TEXT,
                resolved      INTEGER DEFAULT 0,
                would_outcome TEXT,
                would_pnl_rs    REAL,
                notes         TEXT
            );
        """)
        self._migrate_columns()

    def _migrate_columns(self):
        for col, typ in [
            ('mae_rs', 'REAL DEFAULT 0'),
            ('mfe_rs', 'REAL DEFAULT 0'),
            ('slippage_rs', 'REAL DEFAULT 0'),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass

    def record_entry(self, trade: dict, ctx: dict) -> int:
        """Record trade entry with full market context"""
        now   = datetime.now(IST)
        price = ctx.get('bnf_price', 0)
        vwap  = ctx.get('vwap', 0)

        vwap_rel = ('ABOVE' if price > vwap else
                    'BELOW' if price < vwap else 'AT') if vwap else 'UNKNOWN'

        with self._lock:
            cur = self.conn.execute("""
                INSERT INTO trades
                (date, entry_time, option_name, bias, session, hour,
                 day_of_week, bnf_at_entry, bnf_range, score, regime,
                 rsi, vwap_vs_price, entry_prem, sl_prem, tgt_prem)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now.strftime('%Y-%m-%d'),
                now.strftime('%H:%M'),
                trade.get('name', ''),
                ctx.get('bias', ''),
                ctx.get('session', ''),
                now.hour,
                now.strftime('%A'),
                price,
                self._price_range(price),
                ctx.get('score', 0),
                ctx.get('regime', ''),
                ctx.get('rsi', 50),
                vwap_rel,
                trade.get('entry_prem', 0),
                trade.get('sl_prem', 0),
                trade.get('tgt_prem', 0),
            ))
            return cur.lastrowid

    def add_lesson(self, tid: int, lesson: str):
        """Append self-validation lesson to existing trade record"""
        try:
            with self._lock:
                existing = self._get_field(tid, 'lesson') or ''
                combined = f"{existing} | {lesson}" if existing else lesson
                self.conn.execute(
                    "UPDATE trades SET lesson=? WHERE id=?",
                    (combined[:200], tid)
                )
        except Exception:
            pass

    def record_exit(self, tid: int, data: dict):
        """Record exit, classify lesson, update pattern memory"""
        entry_prem = self._get_field(tid, 'entry_prem') or 0
        sl_prem    = self._get_field(tid, 'sl_prem')    or 0
        exit_prem  = data.get('exit_prem', 0)
        pnl_rs     = data.get('pnl_rs', 0)
        pnl_pct    = data.get('pnl_pct', 0)
        outcome    = 'WIN' if pnl_rs > 0 else 'LOSS'
        reason     = data.get('reason', '')
        r_multiple = round(pnl_rs / (entry_prem - sl_prem) / 15, 2) \
                     if (entry_prem - sl_prem) > 0 else 0
        entry_time = self._get_field(tid, 'entry_time') or ''
        hold_min   = self._hold_minutes(entry_time)
        mistake, lesson = self._classify(outcome, reason, data)

        mae_rs = data.get('mae_rs', 0)
        mfe_rs = data.get('mfe_rs', 0)
        slip   = data.get('slippage_rs', 0)
        if data.get('theta_decay'):
            mistake = 'THETA_DECAY'
            lesson  = '📚 Theta decay — direction was OK but time killed premium'

        with self._lock:
            self.conn.execute("""
                UPDATE trades SET
                  exit_time=?, exit_prem=?, pnl_rs=?, pnl_pct=?,
                  r_multiple=?, outcome=?, exit_reason=?,
                  hold_minutes=?, mistake_type=?, lesson=?,
                  mae_rs=?, mfe_rs=?, slippage_rs=?
                WHERE id=?
            """, (datetime.now(IST).strftime('%H:%M'),
                  exit_prem, pnl_rs, pnl_pct, r_multiple,
                  outcome, reason, hold_min, mistake, lesson,
                  mae_rs, mfe_rs, slip, tid))

        self._update_patterns(tid, outcome, pnl_rs)
        self._update_daily_pnl(data.get('date') or datetime.now(IST).strftime('%Y-%m-%d'),
                                outcome, pnl_rs)
        try:
            from src.market_rag import ingest_trade_lesson
            from core.shared_state import STATE
            ctx = STATE.get('market.context') or {}
            ingest_trade_lesson(
                session=self._get_field(tid, 'session') or '',
                bias=self._get_field(tid, 'bias') or '',
                regime=STATE.get('market.regime', ''),
                mistake=mistake,
                lesson=lesson,
                outcome=outcome,
                cpr_class=(ctx.get('cpr') or {}).get('width_class', ''),
            )
        except Exception:
            pass
        return {'outcome': outcome, 'lesson': lesson, 'mistake': mistake, 'pnl_rs': pnl_rs}

    def _update_daily_pnl(self, date_str: str, outcome: str, pnl_rs: float):
        """Write rolling daily P&L summary."""
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT trades, wins, pnl, max_loss FROM daily_pnl WHERE date=?",
                    (date_str,)
                ).fetchone()
                if row:
                    trades, wins, pnl, max_loss = row
                    trades += 1
                    wins += 1 if outcome == 'WIN' else 0
                    pnl = (pnl or 0) + pnl_rs
                    max_loss = min(max_loss or 0, pnl_rs) if pnl_rs < 0 else (max_loss or 0)
                else:
                    trades, wins, pnl = 1, (1 if outcome == 'WIN' else 0), pnl_rs
                    max_loss = pnl_rs if pnl_rs < 0 else 0
                self.conn.execute("""
                    INSERT INTO daily_pnl (date, trades, wins, pnl, max_loss)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(date) DO UPDATE SET
                        trades=?, wins=?, pnl=?, max_loss=?
                """, (date_str, trades, wins, pnl, max_loss,
                      trades, wins, pnl, max_loss))
        except Exception:
            pass

    def _classify(self, outcome, reason, data):
        if outcome == 'WIN':
            if 'TARGET' in reason.upper():
                return 'NONE', '✅ Full target — perfect setup execution'
            if 'TRAIL' in reason.upper():
                return 'NONE', '✅ Trailing SL captured move — momentum trade'
            if 'LEG1' in reason.upper():
                return 'NONE', '✅ Partial exit locked profit — good management'
            return 'NONE', '✅ Profitable trade'

        # Classify losses
        score   = data.get('score', 5)
        session = data.get('session', '')
        regime  = data.get('regime', '')
        rsi     = data.get('rsi', 50)

        if 'EOD' in reason.upper():
            return 'TIMING', '📚 Entered too late — trade needs more time to develop'
        if data.get('mae_rs') and data.get('mfe_rs'):
            if data['mfe_rs'] > abs(data.get('pnl_rs', 0)) and data.get('pnl_rs', 0) < 0:
                return 'SL_TIGHT', '📚 Direction right but SL too tight — MFE exceeded final loss'
        if score < 7:
            return 'LOW_SCORE', '📚 Low confidence setup — raise minimum score threshold'
        if 'LUNCH' in session or 'EOD' in session:
            return 'BAD_SESSION', '📚 Wrong session — avoid lunch and EOD entries'
        if regime == 'RANGING':
            return 'RANGING_MARKET', '📚 Ranging market OB failed — wait for TRENDING regime'
        if rsi > 70:
            return 'OVERBOUGHT', '📚 Entered overbought — wait for RSI to normalize'
        if rsi < 30:
            return 'OVERSOLD', '📚 Entered oversold area — contrary to CE direction'
        return 'MARKET_MOVE', '📚 Valid setup stopped — accept as cost of doing business'

    def _update_patterns(self, tid, outcome, pnl_rs):
        """Update win rates for all pattern dimensions"""
        row = self.conn.execute(
            "SELECT session,hour,day_of_week,score,regime,rsi FROM trades WHERE id=?",
            (tid,)
        ).fetchone()
        if not row:
            return

        session, hour, day, score, regime, rsi = row
        rsi_zone = 'RSI_HIGH' if rsi > 60 else ('RSI_LOW' if rsi < 40 else 'RSI_MID')

        keys = [
            f"session:{session}",
            f"hour:{hour}",
            f"day:{day}",
            f"score:{score}",
            f"regime:{regime}",
            f"rsi:{rsi_zone}",
            # Combined (more specific)
            f"hour:{hour}|session:{session}",
            f"score:{score}|regime:{regime}",
            f"day:{day}|session:{session}",
        ]

        is_win = 1 if outcome == 'WIN' else 0
        now    = datetime.now(IST).strftime('%Y-%m-%d')

        with self._lock:
            for k in keys:
                self.conn.execute("""
                    INSERT INTO pattern_memory
                    (pattern_key,wins,losses,total_pnl,samples,last_seen)
                    VALUES (?,?,?,?,1,?)
                    ON CONFLICT(pattern_key) DO UPDATE SET
                        wins      = wins + ?,
                        losses    = losses + ?,
                        total_pnl = total_pnl + ?,
                        samples   = samples + 1,
                        last_seen = ?
                """, (k, is_win, 1-is_win, pnl_rs, now,
                      is_win, 1-is_win, pnl_rs, now))

    def get_pattern_wr(self, key: str, min_samples: int = 5):
        """Win rate for a pattern (None if < min_samples)"""
        row = self.conn.execute(
            "SELECT wins,losses,samples FROM pattern_memory WHERE pattern_key=?",
            (key,)
        ).fetchone()
        if row and row[2] >= min_samples:
            total = row[0] + row[1]
            return round(row[0] / total * 100, 1) if total > 0 else None
        return None

    def get_pattern_winrate(self, key: str, min_samples: int = 5):
        """Alias used by RiskAgent."""
        return self.get_pattern_wr(key, min_samples)

    def fractional_kelly(self) -> float:
        """
        Fractional Kelly position sizing.
        Returns size multiplier (0.25 to 1.0).
        Research: use quarter-Kelly for new systems.
        """
        rows = self.conn.execute(
            "SELECT outcome, pnl_rs, entry_prem, sl_prem FROM trades "
            "WHERE outcome IS NOT NULL ORDER BY id DESC LIMIT 50"
        ).fetchall()

        if len(rows) < MIN_TRADES_TO_LEARN:
            return KELLY_FRACTION  # Conservative default

        wins  = [r for r in rows if r[0] == 'WIN']
        losses= [r for r in rows if r[0] == 'LOSS']
        p     = len(wins) / len(rows)
        q     = 1 - p

        # Average R:R from actual trades
        avg_win  = sum(r[1] for r in wins)  / max(len(wins), 1)
        avg_loss = abs(sum(r[1] for r in losses)) / max(len(losses), 1)
        b        = avg_win / avg_loss if avg_loss > 0 else 1.0

        # Kelly fraction
        kelly_full = (b * p - q) / b if b > 0 else 0
        kelly_full = max(0, min(kelly_full, 1.0))

        # Apply quarter-Kelly (research-backed conservative multiplier)
        fraction = kelly_full * KELLY_FRACTION
        return round(min(max(fraction, 0.25), 1.0), 2)

    def get_adaptive_thresholds(self) -> dict:
        """
        Calculate thresholds from trading history.
        ONLY adjusts after MIN_TRADES_TO_LEARN trades.
        """
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE outcome IS NOT NULL"
        ).fetchall()

        total = len(rows)
        if total < MIN_TRADES_TO_LEARN:
            return {
                'min_score':      5,
                'max_trades_day': 1,
                'avoid_hours':    [],
                'best_session':   '',
                'win_rate':       0.0,
                'total_trades':   total,
                'kelly':          KELLY_FRACTION,
                'learning_stage': f"EARLY ({total}/{MIN_TRADES_TO_LEARN} trades)"
            }

        wins = [r for r in rows if r[23] == 'WIN']
        wr   = round(len(wins) / total * 100, 1)

        # Score win rates (need ≥10 per score)
        score_wr = defaultdict(lambda: {'w':0,'t':0})
        for r in rows:
            s = r[11]
            score_wr[s]['t'] += 1
            if r[23] == 'WIN': score_wr[s]['w'] += 1
        good_scores = [s for s, v in score_wr.items()
                       if v['t'] >= 10 and v['w']/v['t'] >= 0.60]
        min_score = min(good_scores) if good_scores else 5

        # Avoid hours (need ≥5 samples, wr < 35%)
        hour_stats = defaultdict(lambda: {'w':0,'t':0})
        for r in rows:
            h = r[7]  # hour column
            hour_stats[h]['t'] += 1
            if r[23] == 'WIN': hour_stats[h]['w'] += 1
        avoid_hours = [h for h, v in hour_stats.items()
                       if v['t'] >= 5 and v['w']/v['t'] < 0.35]

        # Scale trades/day (only after 20 recent with 65%+ wr)
        recent = rows[-20:]
        rec_wr = sum(1 for r in recent if r[23]=='WIN') / 20 * 100
        max_trades = 1
        if total >= 40 and rec_wr >= 65: max_trades = 2
        if total >= 60 and rec_wr >= 75: max_trades = 3

        # Best session
        sess_stats = defaultdict(lambda: {'w':0,'t':0})
        for r in rows:
            sess_stats[r[6]]['t'] += 1  # session column
            if r[23] == 'WIN': sess_stats[r[6]]['w'] += 1
        best_sess = max(sess_stats,
                        key=lambda s: sess_stats[s]['w']/max(sess_stats[s]['t'],1),
                        default='')

        return {
            'min_score':      min_score,
            'max_trades_day': max_trades,
            'avoid_hours':    avoid_hours,
            'best_session':   best_sess,
            'win_rate':       wr,
            'total_trades':   total,
            'kelly':          self.fractional_kelly(),
            'learning_stage': 'ACTIVE',
        }

    def weekly_report(self) -> str:
        week_ago = (datetime.now(IST)-timedelta(days=7)).strftime('%Y-%m-%d')
        week = self.conn.execute(
            "SELECT outcome,pnl_rs FROM trades WHERE date>=? AND outcome IS NOT NULL",
            (week_ago,)
        ).fetchall()
        all_ = self.conn.execute(
            "SELECT outcome,pnl_rs FROM trades WHERE outcome IS NOT NULL"
        ).fetchall()

        w_wins = sum(1 for r in week if r[0]=='WIN')
        w_pnl  = round(sum(r[1] for r in week), 0)
        t_wr   = round(sum(1 for r in all_ if r[0]=='WIN')/max(len(all_),1)*100,1)
        thresh = self.get_adaptive_thresholds()

        top_p = self.conn.execute("""
            SELECT pattern_key,
                   ROUND(wins*100.0/(wins+losses),1) wr,
                   wins+losses n
            FROM pattern_memory
            WHERE wins+losses >= 5
            ORDER BY wr DESC LIMIT 5
        """).fetchall()

        lessons = self.conn.execute("""
            SELECT DISTINCT lesson FROM trades
            WHERE date>=? AND lesson IS NOT NULL
            ORDER BY id DESC LIMIT 5
        """, (week_ago,)).fetchall()

        patt_txt = '\n'.join(f"  {p[0]}: {p[1]:.0f}% ({p[2]} trades)"
                             for p in top_p) or "  Still learning..."
        less_txt = '\n'.join(f"  {l[0]}" for l in lessons) or "  No lessons this week"

        from src.brain_metrics import compute_paper_confidence, assess_live_readiness
        from src.learning_scoreboard import format_scoreboard_block, estimate_slippage_from_shadow
        conf  = compute_paper_confidence()
        ready = assess_live_readiness()
        scoreboard = format_scoreboard_block(7)
        slip = estimate_slippage_from_shadow()
        slip_line = f"\n📐 {slip['note']}\n" if slip.get('available') else ''

        body = (
            f"🧠 *Weekly Brain Report*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 This Week: {len(week)} trades | "
            f"{w_wins}W | ₹{w_pnl:,}\n"
            f"📊 All time:  {len(all_)} trades | {t_wr:.1f}% win rate\n"
            f"🎯 Paper confidence: {conf['score']}/100 ({conf['grade']})\n"
            f"🛡️ Live readiness: {ready['reason']}\n\n"
            f"{scoreboard}\n"
            f"{slip_line}\n"
            f"🎯 Adaptive Settings:\n"
            f"  Stage: {thresh['learning_stage']}\n"
            f"  Min score: {thresh['min_score']}\n"
            f"  Max trades/day: {thresh['max_trades_day']}\n"
            f"  Kelly fraction: {thresh['kelly']}\n"
            f"  Avoid hours: {thresh['avoid_hours']}\n\n"
            f"🏆 Best Patterns:\n{patt_txt}\n\n"
            f"📚 Lessons:\n{less_txt}\n\n"
            f"_Learning from every trade_ 🤖"
        )
        try:
            from src.llm_advisor import llm_enabled, weekly_coach_note
            if llm_enabled():
                ai = weekly_coach_note(body)
                if ai:
                    body += f"\n\n🤖 *AI weekly coach:*\n{ai}"
        except Exception:
            pass
        return body

    # helpers
    def _get_field(self, tid, field):
        r = self.conn.execute(f"SELECT {field} FROM trades WHERE id=?", (tid,)).fetchone()
        return r[0] if r else None

    def _hold_minutes(self, entry_time):
        try:
            now = datetime.now(IST)
            e   = datetime.strptime(entry_time, '%H:%M').replace(
                year=now.year, month=now.month, day=now.day, tzinfo=IST)
            return int((now-e).seconds/60)
        except: return 0

    def _price_range(self, p):
        for lo, hi, label in [
            (0,50000,'<50K'),(50000,52000,'50K-52K'),
            (52000,54000,'52K-54K'),(54000,56000,'54K-56K'),
            (56000,58000,'56K-58K'),(58000,60000,'58K-60K')]:
            if lo <= p < hi: return label
        return '>60K'


BRAIN = TraderBrain()


class LearningAgent(threading.Thread):

    def __init__(self, messenger: Messenger):
        super().__init__(daemon=True, name='LearningAgent')
        self.msg   = messenger
        self.brain = BRAIN

    def _push_to_state(self):
        t = self.brain.get_adaptive_thresholds()
        from src.trade_analytics import apply_mistake_auto_rules
        rules = apply_mistake_auto_rules()
        STATE.update('brain', {
            'min_score':      t['min_score'] + rules.get('min_score_boost', 0),
            'max_trades_day': t['max_trades_day'],
            'avoid_hours':    t['avoid_hours'],
            'best_session':   t['best_session'],
            'win_rate':       t['win_rate'],
            'total_trades':   t['total_trades'],
            'kelly':          t['kelly'],
            'learning_stage': t.get('learning_stage', 'EARLY'),
            'sl_widen_pct':   rules.get('sl_widen_pct', 0),
            'block_ranging':  rules.get('block_ranging', False),
            'auto_rule_note': rules.get('note', ''),
        })

    def run(self):
        STATE.set_agent_status('learning', 'RUNNING')
        print("🧠 Learning Agent started")

        last_weekly = -1

        while STATE.get('system.running'):
            try:
                self._push_to_state()
            except Exception as e:
                STATE.add_error(f"Learning Agent: {str(e)[:60]}")
            time.sleep(300)

        STATE.set_agent_status('learning', 'STOPPED')
