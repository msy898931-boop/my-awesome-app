"""A tiny health endpoint, so a web-service host keeps the bot alive.

The bot is a silent polling loop: it opens no socket, so a platform that
expects a web service sees nothing listening and kills the process. This
module serves ``/`` and ``/health`` on ``$PORT`` from a daemon thread, which
is enough for the platform to consider the service healthy. It reports the
loop's own state, so the endpoint is diagnostic rather than decorative.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


def start_health_server(port: int, status: Callable[[], dict]) -> ThreadingHTTPServer:
    """Serve a JSON health document on ``port``. Returns the running server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            if self.path in ("/", "/health", "/healthz"):
                body = json.dumps(status(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
            else:
                body = b'{"error":"not found"}'
                self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            # The platform already logs requests; a line per poll is noise.
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    return server
