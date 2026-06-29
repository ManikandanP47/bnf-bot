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

    def send_with_buttons(self, text: str, buttons: list) -> bool:
        """Send message with inline keyboard buttons.
        buttons: list of rows, each row is list of {"text": ..., "callback_data": ...}
        """
        for i in range(3):
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={
                        'chat_id': self.chat_id,
                        'text': text[:4096],
                        'parse_mode': 'Markdown',
                        'reply_markup': {'inline_keyboard': buttons},
                    },
                    timeout=15
                )
                if resp.json().get('ok'):
                    return True
            except:
                pass
            if i < 2:
                time.sleep(2 * (i + 1))
        return False
