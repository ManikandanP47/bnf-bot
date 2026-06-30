#!/usr/bin/env bash
# Weekly offsite backup — run from cron or Mac: scp brain + models + evidence to local.
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/bnf-bot}"
DEST="${1:-$HOME/bnf-bot-offsite-backups}"
STAMP=$(date +%Y-%m-%d)
mkdir -p "$DEST/$STAMP"

cd "$BOT_DIR"
for f in trader_brain.db sim_evidence.jsonl daily_zone.json; do
  [[ -f "$f" ]] && cp -a "$f" "$DEST/$STAMP/"
done
[[ -d models ]] && cp -a models "$DEST/$STAMP/"
[[ -d backups ]] && cp -a backups "$DEST/$STAMP/backups_snapshot"

echo "Offsite backup → $DEST/$STAMP"
ls -la "$DEST/$STAMP"
