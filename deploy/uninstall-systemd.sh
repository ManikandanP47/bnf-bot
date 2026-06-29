#!/bin/bash
# Remove bnf-bot systemd service.
# Run: sudo ./deploy/uninstall-systemd.sh

set -euo pipefail

SERVICE_NAME="bnf-bot"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/uninstall-systemd.sh"
  exit 1
fi

systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

echo "Removed ${SERVICE_NAME} service."
