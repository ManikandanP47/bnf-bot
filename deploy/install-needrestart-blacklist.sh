#!/usr/bin/env bash
# Blacklist bnf-bot from needrestart auto-restarts after apt library upgrades.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_NAME="bnf-bot.conf"
TARGET="/etc/needrestart/conf.d/${CONF_NAME}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/install-needrestart-blacklist.sh"
  exit 1
fi

install -m 0644 "${SCRIPT_DIR}/needrestart-bnf-bot.conf" "${TARGET}"
echo "Installed ${TARGET}"
echo "bnf-bot will NOT be auto-restarted by unattended-upgrades / needrestart."
echo "After lib upgrades, restart manually after market close:"
echo "  sudo systemctl restart bnf-bot"
