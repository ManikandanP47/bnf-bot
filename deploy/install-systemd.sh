#!/bin/bash
# Install bnf-bot as a systemd service (one instance, auto-restart on crash).
# Run from repo root: sudo ./deploy/install-systemd.sh

set -euo pipefail

SERVICE_NAME="bnf-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/install-systemd.sh"
  exit 1
fi

if [[ ! -f "${BOT_DIR}/venv/bin/python" ]]; then
  echo "Error: ${BOT_DIR}/venv not found. Create venv and pip install -r requirements.txt first."
  exit 1
fi

if [[ ! -f "${BOT_DIR}/.env" ]]; then
  echo "Warning: ${BOT_DIR}/.env missing — create it before starting the bot."
fi

BOT_USER="$(stat -c '%U' "${BOT_DIR}" 2>/dev/null || stat -f '%Su' "${BOT_DIR}")"

echo "Installing ${SERVICE_NAME} service"
echo "  Directory: ${BOT_DIR}"
echo "  User:      ${BOT_USER}"

sed -e "s|@BOT_DIR@|${BOT_DIR}|g" -e "s|@BOT_USER@|${BOT_USER}|g" \
  "${SCRIPT_DIR}/bnf-bot.service" > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

# Stop manual nohup / duplicate python main.py processes
echo "Stopping any existing main.py processes..."
pkill -f "[p]ython.*main.py" 2>/dev/null || true
sleep 2
pkill -9 -f "[p]ython.*main.py" 2>/dev/null || true
sleep 1

systemctl restart "${SERVICE_NAME}"

echo ""
systemctl status "${SERVICE_NAME}" --no-pager || true
echo ""
echo "Done. Commands:"
echo "  systemctl status ${SERVICE_NAME}"
echo "  systemctl stop ${SERVICE_NAME}"
echo "  systemctl restart ${SERVICE_NAME}"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo "  tail -f ${BOT_DIR}/bot.log"
