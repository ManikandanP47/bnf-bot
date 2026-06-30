"""Minimal HTTP health endpoint for external uptime monitors."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HEALTH_PORT = int(os.getenv('HEALTH_PORT', '8080'))
HEALTH_ENABLED = os.getenv('HEALTH_ENABLED', 'true').lower() == 'true'


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path not in ('/', '/health', '/healthz'):
            self.send_response(404)
            self.end_headers()
            return
        try:
            from core.shared_state import STATE
            from src.db_persistence import get_table_counts
            counts = get_table_counts()
            body = {
                'ok': STATE.get('system.running', False),
                'agents': STATE.get('system.agent_status', {}),
                'market_open': STATE.get('system.market_open', False),
                'db': counts.get('db_exists', False),
                'shadow_trades': counts.get('shadow_trades', 0),
            }
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(str(e).encode())


def start_health_server():
    if not HEALTH_ENABLED:
        return
    try:
        server = HTTPServer(('0.0.0.0', HEALTH_PORT), _HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True, name='HealthServer')
        t.start()
        print(f"🏥 Health endpoint :{HEALTH_PORT}/health")
    except OSError as e:
        print(f"⚠️ Health server skipped: {e}")
