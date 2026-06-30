#!/usr/bin/env bash
# Safe one-command update on the server (git pull + deps + env defaults + restart).
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/bnf-bot}"
cd "$BOT_DIR"

echo "==> Pulling latest main..."
git pull origin main

echo "==> Installing dependencies..."
./venv/bin/pip install -q -r requirements.txt

if [[ -f .env ]]; then
  ensure_var() {
    local key="$1" val="$2"
    if ! grep -q "^${key}=" .env 2>/dev/null; then
      echo "${key}=${val}" >> .env
      echo "    + added ${key}=${val}"
    fi
  }
  echo "==> Ensuring training / ML / sim defaults in .env..."
  ensure_var ML_LEARNING true
  ensure_var ML_MIN_SAMPLES 25
  ensure_var ML_NN_MIN_SAMPLES 100
  ensure_var ML_NN_ENABLED true
  ensure_var MARKET_SIM true
  ensure_var SIM_MAX_PER_DAY 15
  ensure_var SIM_SCAN_MINUTES 4
  ensure_var SIM_MAX_OPEN 2
  ensure_var SIM_MIN_GAP_MIN 8
  ensure_var SIM_ONLY_DAYS 14
  ensure_var PAPER_PHASE_DAYS 14
  ensure_var LEARNING_PHASE_DAYS 14
  ensure_var LEARNING_MAX_TRADES_DAY 2
  ensure_var POST_LEARNING_MAX_TRADES_DAY 2
  ensure_var SHADOW_MIN_WR 40
  ensure_var SIM_TELEGRAM_QUIET true
  ensure_var SIM_SCAN_LOG true
  ensure_var TELEGRAM_MIRROR_ENABLED true
  ensure_var MIN_PAPER_TRADES 20
  ensure_var MIN_WIN_RATE 56
  ensure_var MIN_RECENT_WIN_RATE 50
  ensure_var RECENT_TRADES_WINDOW 10
  ensure_var MIN_WIN_LOSS_RATIO 0.7
  ensure_var MIN_EXPECTANCY_RS 100
fi

echo "==> Restarting bnf-bot..."
sudo systemctl restart bnf-bot
sleep 2

if systemctl is-active --quiet bnf-bot; then
  echo "✅ bnf-bot is active"
else
  echo "❌ bnf-bot failed to start — check: journalctl -u bnf-bot -n 40"
  exit 1
fi

echo "==> Last log lines:"
tail -8 bot.log 2>/dev/null || journalctl -u bnf-bot -n 8 --no-pager
