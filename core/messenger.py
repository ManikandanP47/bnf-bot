"""Messenger — Reliable Telegram with retry"""
import requests, time, os


def _mirror_out(text: str, kind: str = 'text'):
    try:
        from src.telegram_mirror import mirror_message
        mirror_message('out', text, kind=kind)
    except Exception:
        pass


class Messenger:
    def __init__(self):
        self.token   = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

    def _post_message(self, payload: dict) -> tuple[bool, str]:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=payload,
                timeout=15,
            )
            data = resp.json()
            if data.get('ok'):
                return True, ''
            return False, str(data.get('description', resp.text))[:200]
        except Exception as e:
            return False, str(e)[:200]

    def send(self, text: str, retries: int = 3, parse_mode: str = 'Markdown') -> bool:
        body = text[:4096]
        modes = [parse_mode] if parse_mode else [None]
        if parse_mode:
            modes.append(None)  # fallback: plain text if Markdown breaks

        for attempt in range(retries):
            for mode in modes:
                payload = {'chat_id': self.chat_id, 'text': body}
                if mode:
                    payload['parse_mode'] = mode
                ok, err = self._post_message(payload)
                if ok:
                    _mirror_out(body, kind='buttons' if 'reply_markup' in payload else 'text')
                    return True
                if mode and 'parse' in err.lower():
                    print(f"⚠️  Telegram Markdown failed, retrying plain text: {err}")
                    continue
                if err:
                    print(f"⚠️  Telegram send failed: {err}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        return False

    def send_with_buttons(self, text: str, buttons: list) -> bool:
        """Send message with inline keyboard buttons.
        buttons: list of rows, each row is list of {"text": ..., "callback_data": ...}
        """
        body = text[:4096]
        attempts = [
            {'parse_mode': 'Markdown', 'reply_markup': {'inline_keyboard': buttons}},
            {'reply_markup': {'inline_keyboard': buttons}},
        ]
        for i, extra in enumerate(attempts):
            payload = {'chat_id': self.chat_id, 'text': body, **extra}
            ok, err = self._post_message(payload)
            if ok:
                _mirror_out(body, kind='buttons')
                return True
            if err:
                print(f"⚠️  Telegram buttons send failed: {err}")
            if i < len(attempts) - 1:
                time.sleep(2)
        return False
