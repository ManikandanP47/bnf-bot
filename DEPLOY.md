# Deploy to DigitalOcean Server

## One-time setup on your server

SSH into server and run these commands:

```bash
# 1. Update server
apt update && apt upgrade -y

# 2. Install Python
apt install python3 python3-pip git -y

# 3. Clone your repo
git clone https://github.com/ManikandanP47/bnf-bot.git
cd bnf-bot

# 4. Install dependencies (Ubuntu 24+ needs venv — not system pip)
apt install -y python3-venv python3-full
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 5. Create .env file with your credentials
nano .env
# Or copy from Mac: scp ~/Downloads/bnf-bot-main/.env root@YOUR_IP:~/bnf-bot/.env

# 6. Test Groww + Telegram
./venv/bin/python test_groww_all_apis.py
./venv/bin/python test_telegram.py
```

## Run with systemd (recommended)

One instance only, auto-restart on crash, starts after reboot.

```bash
cd ~/bnf-bot
git pull origin main
chmod +x deploy/install-systemd.sh deploy/uninstall-systemd.sh
sudo ./deploy/install-systemd.sh
```

The installer will:
- Register `bnf-bot.service`
- Kill any old `nohup` / duplicate `main.py` processes
- Start the bot

### Daily commands

```bash
systemctl status bnf-bot      # running?
systemctl restart bnf-bot     # after git pull or .env change
systemctl stop bnf-bot        # stop trading
journalctl -u bnf-bot -f      # live logs (systemd)
tail -f ~/bnf-bot/bot.log     # same output in bot.log
```

### Update after git pull

**One command (recommended):**

```bash
cd ~/bnf-bot
chmod +x deploy/update.sh
./deploy/update.sh
```

This pulls `main`, installs deps, adds missing ML/sim env defaults, and restarts systemd.

**Manual:**

```bash
cd ~/bnf-bot
git pull origin main
./venv/bin/pip install -r requirements.txt
sudo systemctl restart bnf-bot
```

### Remove systemd service

```bash
sudo ./deploy/uninstall-systemd.sh
```

### Restore from backup

```bash
cd ~/bnf-bot
cp backups/YYYY-MM-DD/trader_brain.db ./trader_brain.db
cp -r backups/YYYY-MM-DD/models ./models   # if ML model was backed up
sudo systemctl restart bnf-bot
```

Note: JWT token cache is **not** backed up (security). Bot re-authenticates via TOTP on restart.

## Manual run (not recommended)

Only use if you are not using systemd:

```bash
# Check nothing is already running first!
ps aux | grep main.py

pkill -f "main.py" 2>/dev/null
nohup ./venv/bin/python main.py >> bot.log 2>&1 &
tail -f bot.log
```

**Never** run `nohup` while `systemctl status bnf-bot` shows active — you will get duplicate bots.

## Telegram commands

`/status` `/journal` `/readiness` `/funnel` `/execute` `/skip` `/pause` `/resume`
