"""A tiny health endpoint, so a web-service host keeps the bot alive.

The bot is a silent polling loop: it opens no socket, so a platform that
expects a web service sees nothing listening and kills the process. This
module serves ``/`` and ``/health`` on ``$PORT`` from a daemon thread, which
is enough for the platform to consider the service healthy. It reports the
loop's own state, so the endpoint is diagnostic rather than decorative.

It also keeps the service from falling asleep. Render spins a Free web service
down after 15 minutes without inbound traffic, and an *external* pinger is not
a reliable guard: a GitHub Actions ``schedule`` is best-effort and can be
delayed well past that window (observed: zero scheduled runs over two hours).
The service therefore pings its own public URL. That request is inbound traffic
as far as the platform is concerned, which is exactly what resets the timer.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .net import open_url


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

    # Binding all interfaces is the point: the host routes external traffic to
    # this container port, so loopback would make the service unreachable and the
    # health check would fail by design. The payload is non-sensitive (uptime,
    # cycle count, poll interval, whether AI is on).
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # nosec B104
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    return server


# Comfortably under Render's 15-minute idle window, so a missed tick or two
# still leaves the service awake.
SELF_PING_SECONDS = 600.0


def ping_once(url: str) -> bool:
    """Request the service's own ``/health``. True when it answered.

    The request must travel through the public hostname to count as inbound
    traffic; a call to ``localhost`` never leaves the box and would not reset
    the platform's idle timer.
    """
    target = f"{url.rstrip('/')}/health"
    if not url.strip():
        return False
    try:
        open_url(target, timeout=30).read()
        return True
    except Exception:
        return False


def start_self_ping(url: str, interval: float = SELF_PING_SECONDS) -> None:
    """Ping our own public URL on a timer, so the service never idles out.

    Failures are ignored on purpose: the next tick retries, and the endpoint
    being briefly unreachable is not worth a crash.
    """
    if not url.strip():
        return

    def loop() -> None:
        while True:
            time.sleep(interval)
            ping_once(url)

    threading.Thread(target=loop, daemon=True, name="self-ping").start()
