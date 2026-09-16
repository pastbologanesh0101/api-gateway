"""A tiny in-process HTTP server used as a stand-in upstream for tests/demos.

Not part of the gateway itself — this simulates a backend service so the
gateway's proxying, auth, and rate-limiting behavior can be exercised without
any real network dependency.
"""

from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer


class EchoUpstreamHandler(BaseHTTPRequestHandler):
    """Responds to any request with a small JSON payload describing it."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass

    def _respond(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        payload = json.dumps(
            {
                "upstream": self.server.upstream_name,  # type: ignore[attr-defined]
                "method": method,
                "path": self.path,
                "body": body.decode("utf-8", errors="replace"),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._respond("GET")

    def do_POST(self) -> None:
        self._respond("POST")

    def do_PUT(self) -> None:
        self._respond("PUT")

    def do_DELETE(self) -> None:
        self._respond("DELETE")


class UpstreamHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_upstream(name: str, host: str = "127.0.0.1", port: int = 0) -> UpstreamHTTPServer:
    """Build (but do not start) a tiny echo upstream server for tests/demos."""
    server = UpstreamHTTPServer((host, port), EchoUpstreamHandler)
    server.upstream_name = name  # type: ignore[attr-defined]
    return server
