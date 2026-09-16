"""The reverse-proxy HTTP gateway.

Built on the standard library's ``http.server`` / ``socketserver`` so the
project has zero runtime dependencies. Requests are:

1. Matched against configured routes by path prefix (404 if no match).
2. Authenticated via the ``X-API-Key`` header (401 if missing/invalid).
3. Rate limited per API key via a token bucket (429 if exceeded).
4. Proxied to the matched upstream, and the response streamed back.
5. Logged as a single structured line (method, path, status, latency).
"""

from __future__ import annotations

import socketserver
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import GatewayConfig
from .logging_utils import RequestLogger
from .token_bucket import TokenBucketRateLimiter

# Headers we strip when forwarding, either direction (hop-by-hop / sizing
# headers that must not be blindly copied across a proxy boundary).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


class GatewayState:
    """Shared, thread-safe state used by every request handler instance."""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.limiter = TokenBucketRateLimiter(
            capacity=config.rate_limit_capacity,
            refill_rate=config.rate_limit_refill_per_sec,
        )
        self.logger = RequestLogger(log_file=config.log_file)


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """Handles one HTTP request and proxies it according to gateway state."""

    server_version = "APIGateway/0.1"
    protocol_version = "HTTP/1.1"

    # Populated per-server via server.gateway_state (see build_server below).
    gateway_state: GatewayState

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib sig
        # Silence BaseHTTPRequestHandler's default stderr access logging;
        # we do our own structured logging instead.
        pass

    # -- HTTP verb entry points -------------------------------------------------
    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    # -- Core logic ---------------------------------------------------------
    def _handle(self, method: str) -> None:
        start = time.monotonic()
        state: GatewayState = self.gateway_state
        config = state.config
        path = self.path

        api_key = self.headers.get("X-API-Key")
        client_id = api_key or self.client_address[0]

        status: int
        body: bytes = b""

        if api_key is None or api_key not in config.api_keys:
            status = HTTPStatus.UNAUTHORIZED
            body = b'{"error": "missing or invalid API key"}'
            self._send_json(status, body)
        elif not state.limiter.allow(client_id):
            status = HTTPStatus.TOO_MANY_REQUESTS
            body = b'{"error": "rate limit exceeded"}'
            self._send_json(status, body, extra_headers={"Retry-After": "1"})
        else:
            match = config.match_route(path)
            if match is None:
                status = HTTPStatus.NOT_FOUND
                body = b'{"error": "no route for path"}'
                self._send_json(status, body)
            else:
                route, remainder = match
                status = self._proxy(route.upstream, remainder, method)

        latency_ms = (time.monotonic() - start) * 1000.0
        state.logger.log(
            method=method,
            path=path,
            client=client_id,
            status=int(status),
            latency_ms=latency_ms,
        )

    def _proxy(self, upstream_base: str, remainder: str, method: str) -> int:
        """Forward the request to the upstream and stream back the response.

        Returns the status code sent to the client.
        """
        config = self.gateway_state.config
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        request_body = self.rfile.read(content_length) if content_length else None

        target_url = upstream_base + remainder

        forward_headers = {
            k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP
        }

        req = urllib.request.Request(
            target_url, data=request_body, headers=forward_headers, method=method
        )
        try:
            with urllib.request.urlopen(
                req, timeout=config.request_timeout
            ) as resp:
                resp_status = resp.status
                resp_body = resp.read()
                resp_headers = {
                    k: v
                    for k, v in resp.headers.items()
                    if k.lower() not in _HOP_BY_HOP
                }
        except urllib.error.HTTPError as exc:
            resp_status = exc.code
            resp_body = exc.read()
            resp_headers = {
                k: v for k, v in exc.headers.items() if k.lower() not in _HOP_BY_HOP
            } if exc.headers else {}
        except (urllib.error.URLError, OSError):
            resp_status = HTTPStatus.BAD_GATEWAY
            resp_body = b'{"error": "upstream unavailable"}'
            resp_headers = {"Content-Type": "application/json"}

        self.send_response(resp_status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)
        return resp_status

    def _send_json(
        self, status: int, body: bytes, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


class GatewayHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server so slow upstreams don't block other clients."""

    daemon_threads = True
    allow_reuse_address = True


def build_server(config: GatewayConfig, host: str = "127.0.0.1", port: int = 0) -> GatewayHTTPServer:
    """Construct (but do not start) a gateway server bound to host:port.

    Passing port=0 binds to a free ephemeral port (useful for tests).
    """
    state = GatewayState(config)

    # Attach shared state as a class attribute on a per-server subclass so
    # multiple servers in the same process (e.g. in tests) don't collide.
    handler_cls = type(
        "BoundGatewayRequestHandler", (GatewayRequestHandler,), {"gateway_state": state}
    )

    server = GatewayHTTPServer((host, port), handler_cls)
    server.gateway_state = state  # convenience accessor
    return server
