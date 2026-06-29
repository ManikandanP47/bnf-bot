"""Messenger — Reliable Telegram with retry"""
import requests, time, os

class Messenger:
    def __init__(self):
        self.token   = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

    def send(self, text: str, retries: int = 3) -> bool:
        for i in range(retries):
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={'chat_id': self.chat_id,
                          'text': text[:4096], 'parse_mode': 'Markdown'},
                    timeout=15
                )
                if resp.json().get('ok'):
                    return True
            except:
                pass
            if i < retries - 1:
                time.sleep(2 * (i + 1))
        return False
