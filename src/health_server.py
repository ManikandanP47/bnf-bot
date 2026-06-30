"""Health endpoint — delegates to web_server (dashboard + /health)."""

from src.web_server import start_health_server, start_web_server

__all__ = ['start_health_server', 'start_web_server']
