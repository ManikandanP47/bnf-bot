"""Web server — health check + training dashboard UI + JSON API."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_PORT = int(os.getenv('WEB_PORT', os.getenv('HEALTH_PORT', '8080')))
WEB_ENABLED = os.getenv('WEB_ENABLED', os.getenv('HEALTH_ENABLED', 'true')).lower() == 'true'
DASHBOARD_TOKEN = os.getenv('DASHBOARD_TOKEN', '')
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / 'dashboard'


def _authorized(handler) -> bool:
    if not DASHBOARD_TOKEN:
        return True
    qs = parse_qs(urlparse(handler.path).query)
    token = (qs.get('token') or [''])[0]
    if token == DASHBOARD_TOKEN:
        return True
    auth = handler.headers.get('Authorization', '')
    if auth == f'Bearer {DASHBOARD_TOKEN}':
        return True
    return False


def _json_response(handler, code: int, data: dict):
    payload = json.dumps(data, default=str).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class _WebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ('/health', '/healthz'):
            return self._health()

        if path.startswith('/api/'):
            if not _authorized(self):
                return _json_response(self, 401, {'error': 'unauthorized'})
            return self._api(path)

        if path in ('/', '/dashboard', '/dashboard/'):
            if not _authorized(self):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'Dashboard: add ?token=YOUR_DASHBOARD_TOKEN')
                return
            return self._serve_file('index.html', 'text/html')

        # CSS/JS are public (no secrets); API + HTML page stay token-protected
        if path.startswith('/dashboard/'):
            rel = path[len('/dashboard/'):]
            if rel.endswith('.css'):
                return self._serve_file(rel, 'text/css')
            if rel.endswith('.js'):
                return self._serve_file(rel, 'application/javascript')

        self.send_response(404)
        self.end_headers()

    def _health(self):
        try:
            from core.shared_state import STATE
            from src.db_persistence import get_table_counts
            body = {
                'ok': STATE.get('system.running', False),
                'agents': STATE.get('system.agent_status', {}),
                'market_open': STATE.get('system.market_open', False),
                'db': get_table_counts().get('db_exists', False),
            }
            _json_response(self, 200, body)
        except Exception as e:
            _json_response(self, 503, {'ok': False, 'error': str(e)})

    def _api(self, path: str):
        try:
            if path == '/api/v1/snapshot':
                from src.dashboard_api import build_dashboard_payload
                return _json_response(self, 200, build_dashboard_payload())
            if path == '/api/v1/market':
                from src.dashboard_api import build_market_payload
                return _json_response(self, 200, build_market_payload())
            if path == '/api/v1/training':
                from src.dashboard_api import build_training_payload
                return _json_response(self, 200, build_training_payload())
            _json_response(self, 404, {'error': 'not found'})
        except Exception as e:
            _json_response(self, 500, {'error': str(e)})

    def _serve_file(self, name: str, content_type: str):
        fp = DASHBOARD_DIR / name
        if not fp.exists() or not fp.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_web_server():
    if not WEB_ENABLED:
        return
    try:
        server = HTTPServer(('0.0.0.0', WEB_PORT), _WebHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True, name='WebServer')
        t.start()
        tok = ' (token required)' if DASHBOARD_TOKEN else ' (set DASHBOARD_TOKEN!)'
        print(f"📊 Dashboard http://0.0.0.0:{WEB_PORT}/dashboard{tok}")
    except OSError as e:
        print(f"⚠️ Web server skipped: {e}")


def start_health_server():
    """Backward-compatible alias."""
    start_web_server()
