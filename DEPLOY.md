# Deploy to DigitalOcean Server

## One-time setup on your server (64.227.177.10)

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
# Paste the .env contents here

# 6. Test Groww + Telegram
./venv/bin/python test_groww_all_apis.py

# 7. Run bot (paper mode first)
./venv/bin/python main.py

# 8. Run forever (even after logout)
pkill -f "main.py" 2>/dev/null
nohup ./venv/bin/python main.py >> bot.log 2>&1 &
echo "Bot running in background"
tail -f bot.log
```

## Check if bot is running
```bash
ps aux | grep main.py
tail -f bot.log
```

## Update after git pull
```bash
cd ~/bnf-bot
git pull origin main
./venv/bin/pip install -r requirements.txt
pkill -f "main.py" 2>/dev/null
nohup ./venv/bin/python main.py >> bot.log 2>&1 &
```

## Stop bot
```bash
pkill -f "main.py"
```
