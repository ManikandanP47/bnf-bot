# Staging environment

Use a second DigitalOcean droplet (or local Docker) before deploying to production.

## Staging droplet setup

```bash
git clone https://github.com/ManikandanP47/bnf-bot.git
cd bnf-bot
cp .env.template .env
# Use PAPER_MODE=true, separate TELEGRAM_CHAT_ID (staging channel)
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
sudo ./deploy/install-systemd.sh
```

## Docker (local staging)

```bash
cp .env.template .env
docker build -t bnf-bot .
docker run --env-file .env -p 8080:8080 -v $(pwd)/data:/app/data bnf-bot
```

Mount a volume for `trader_brain.db` and `sim_evidence.jsonl` so container restarts keep data.

## Deploy flow

1. Push to `main` → GitHub Actions CI runs
2. Deploy to **staging** → run `./deploy/smoke_test.sh`
3. Paper trade 1–2 days on staging Telegram
4. `./deploy/update.sh` on **production**

## Health check

```bash
curl http://127.0.0.1:8080/health
```

Point UptimeRobot at `http://YOUR_IP:8080/health` (open port 8080 in firewall if needed).

## Offsite backup (weekly cron on server)

```bash
0 20 * * 0 cd ~/bnf-bot && ./scripts/offsite_backup.sh /root/offsite-backups
```

Copy `/root/offsite-backups` to your Mac periodically.
