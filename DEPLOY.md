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

# 4. Install dependencies
pip3 install -r requirements.txt

# 5. Create .env file with your credentials
nano .env
# Paste the .env contents here

# 6. Run bot (paper mode first)
python3 main.py

# 7. Run forever (even after logout)
nohup python3 main.py > bot.log 2>&1 &
echo "Bot running in background"
tail -f bot.log
```

## Check if bot is running
```bash
ps aux | grep main.py
tail -f bot.log
```

## Stop bot
```bash
kill $(ps aux | grep main.py | awk '{print $2}')
```
