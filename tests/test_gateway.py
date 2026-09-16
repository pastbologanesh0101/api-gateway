"""Unit tests for the API Gateway.

Everything runs in-process on ephemeral localhost ports — no external
network access and no dependency on any other repo/service.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from api_gateway.config import GatewayConfig, Route
from api_gateway.gateway import build_server
from api_gateway.testing_upstream import build_upstream


class GatewayTestCase(unittest.TestCase):
    """Base class that spins up demo upstreams + a configured gateway."""

    API_KEY = "test-key-123"
    OTHER_KEY = "test-key-456"

    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_a = build_upstream("service-a")
        cls.upstream_b = build_upstream("service-b")
        cls._threads = []
        for srv in (cls.upstream_a, cls.upstream_b):
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            cls._threads.append(t)

        a_port = cls.upstream_a.server_address[1]
        b_port = cls.upstream_b.server_address[1]

        cls.config = GatewayConfig(
            routes=[
                Route(prefix="/service-a", upstream=f"http://127.0.0.1:{a_port}"),
                Route(prefix="/service-b", upstream=f"http://127.0.0.1:{b_port}"),
            ],
            api_keys={cls.API_KEY, cls.OTHER_KEY},
            rate_limit_capacity=3,
            rate_limit_refill_per_sec=0.001,  # effectively no refill during a test
        )
        cls.config.log_file = None
        cls.server = build_server(cls.config, port=0)
        cls.server.gateway_state.logger.start_capture()
        cls.gateway_port = cls.server.server_address[1]
        cls._server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls._server_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.upstream_a.shutdown()
        cls.upstream_a.server_close()
        cls.upstream_b.shutdown()
        cls.upstream_b.server_close()

    def setUp(self) -> None:
        # Fresh rate-limiter state + log capture for every test.
        self.server.gateway_state.limiter.reset()
        self.server.gateway_state.logger.start_capture()

    def gateway_url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.gateway_port}{path}"

    def request(self, path: str, method: str = "GET", api_key: str | None = None, body: bytes | None = None):
        headers = {}
        if api_key is not None:
            headers["X-API-Key"] = api_key
        req = urllib.request.Request(
            self.gateway_url(path), data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


class TestProxying(GatewayTestCase):
    def test_valid_request_is_proxied_to_correct_upstream(self):
        status, body = self.request("/service-a/ping", api_key=self.API_KEY)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["upstream"], "service-a")
        self.assertEqual(payload["path"], "/ping")
        self.assertEqual(payload["method"], "GET")

    def test_valid_request_routes_to_second_service(self):
        status, body = self.request("/service-b/health", api_key=self.OTHER_KEY)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["upstream"], "service-b")
        self.assertEqual(payload["path"], "/health")

    def test_unknown_path_prefix_returns_404(self):
        status, body = self.request("/service-z/anything", api_key=self.API_KEY)
        self.assertEqual(status, 404)
        payload = json.loads(body)
        self.assertIn("error", payload)


class TestAuth(GatewayTestCase):
    def test_missing_api_key_returns_401(self):
        status, body = self.request("/service-a/ping", api_key=None)
        self.assertEqual(status, 401)
        payload = json.loads(body)
        self.assertIn("error", payload)

    def test_invalid_api_key_returns_401(self):
        status, body = self.request("/service-a/ping", api_key="not-a-real-key")
        self.assertEqual(status, 401)


class TestRateLimiting(GatewayTestCase):
    def test_rate_limit_enforced_returns_429(self):
        # capacity=3 tokens, negligible refill rate configured in setUpClass.
        statuses = []
        for _ in range(5):
            status, _ = self.request("/service-a/ping", api_key=self.API_KEY)
            statuses.append(status)

        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertIn(429, statuses[3:])

    def test_rate_limit_is_tracked_per_api_key(self):
        # Exhaust bucket for API_KEY...
        for _ in range(3):
            self.request("/service-a/ping", api_key=self.API_KEY)
        exhausted_status, _ = self.request("/service-a/ping", api_key=self.API_KEY)
        self.assertEqual(exhausted_status, 429)

        # ...OTHER_KEY should still have its own fresh bucket.
        fresh_status, _ = self.request("/service-a/ping", api_key=self.OTHER_KEY)
        self.assertEqual(fresh_status, 200)


class TestLogging(GatewayTestCase):
    def test_request_logging_captures_expected_fields(self):
        self.request("/service-a/widgets", api_key=self.API_KEY)
        records = self.server.gateway_state.logger.captured()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.path, "/service-a/widgets")
        self.assertEqual(record.status, 200)
        self.assertGreaterEqual(record.latency_ms, 0.0)
        formatted = record.format()
        self.assertIn("method=GET", formatted)
        self.assertIn("path=/service-a/widgets", formatted)
        self.assertIn("status=200", formatted)
        self.assertIn("latency_ms=", formatted)

    def test_logging_records_unauthorized_requests_too(self):
        self.request("/service-a/widgets", api_key=None)
        records = self.server.gateway_state.logger.captured()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, 401)


if __name__ == "__main__":
    unittest.main()
