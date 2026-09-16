# API Gateway

A small, self-contained reverse-proxy API gateway written in pure Python
(standard library only — no Flask, no third-party HTTP framework). It sits
in front of one or more backend services and provides:

- **Path-based routing** — requests to `/service-a/*` are forwarded to one
  upstream, `/service-b/*` to another, based on configured prefixes.
- **API-key authentication** — every request must include a valid
  `X-API-Key` header, or it's rejected with `401`.
- **Per-client rate limiting** — a token-bucket limiter (one bucket per API
  key / client IP) returns `429 Too Many Requests` once a client's burst
  budget is exhausted.
- **Structured request logging** — every request is logged as a single line
  with method, path, client, status, and latency, to stdout or a file.

## Architecture

```
client --> GatewayRequestHandler --> [auth check] --> [rate limit check] --> [route match] --> upstream service
                                         |401              |429                  |404
```

- `api_gateway/config.py` — `GatewayConfig` and `Route` dataclasses. A
  `Route` maps a path prefix to an upstream base URL. `GatewayConfig.match_route`
  does longest-prefix matching against the path.
- `api_gateway/token_bucket.py` — `TokenBucketRateLimiter`: a minimal,
  thread-safe, per-client token bucket (`capacity` tokens, refilling at
  `refill_rate` tokens/second).
- `api_gateway/logging_utils.py` — `RequestLogger` / `RequestLogRecord`:
  structured `key=value` log lines, with an in-memory capture mode used by
  the test suite to make assertions on logged fields.
- `api_gateway/gateway.py` — `GatewayRequestHandler` (a
  `BaseHTTPRequestHandler`) implements the auth -> rate-limit -> route ->
  proxy pipeline described above, and `build_server()` wires it all together
  into a `ThreadingHTTPServer`.
- `api_gateway/testing_upstream.py` — a tiny echo HTTP server used as a
  stand-in "upstream" for tests and the CLI demo, so the gateway can be
  fully exercised without any real backend services or network access.
- `api_gateway/__main__.py` — CLI entry point (`python -m api_gateway`) that
  starts the gateway plus two demo upstreams for interactive testing with
  curl.

Request flow for `_handle()` in `gateway.py`:

1. Check `X-API-Key` header against `config.api_keys` → `401` if missing/invalid.
2. Check the token bucket for that client (API key, or IP if no key) →
   `429` if no tokens available.
3. Match the request path against configured route prefixes → `404` if no
   route matches.
4. Forward the request (method, headers minus hop-by-hop ones, body) to the
   matched upstream via `urllib.request`, stream the response back.
5. Log one structured line with method, path, client, status, latency (ms).

## Configuring routes and keys

Routes and keys are plain Python objects — there's no separate config file
format to learn. Build a `GatewayConfig` and pass it to `build_server`:

```python
from api_gateway.config import GatewayConfig, Route
from api_gateway.gateway import build_server

config = GatewayConfig(
    routes=[
        Route(prefix="/service-a", upstream="http://127.0.0.1:9001"),
        Route(prefix="/service-b", upstream="http://internal-svc-b:8080"),
    ],
    api_keys={"my-secret-key-1", "my-secret-key-2"},
    rate_limit_capacity=20,       # burst size (tokens)
    rate_limit_refill_per_sec=5,  # tokens added back per second
    log_file=None,                # None => log to stdout; or "gateway.log"
)

server = build_server(config, host="0.0.0.0", port=8080)
server.serve_forever()
```

A request to `/service-a/orders/42` with `Route(prefix="/service-a",
upstream="http://127.0.0.1:9001")` configured is forwarded to
`http://127.0.0.1:9001/orders/42`.

## Running

Requires only Python 3.11+ (standard library only, no `pip install` needed
to run the gateway itself).

Start the built-in demo (gateway + two in-process demo upstreams, on port
8080 by default):

```bash
python -m api_gateway --port 8080
```

This prints the demo API keys and example curl commands. Try it:

```bash
# Missing API key -> 401
curl -i http://127.0.0.1:8080/service-a/ping

# Valid key -> proxied to the demo "service-a" upstream -> 200
curl -i -H "X-API-Key: demo-key-1" http://127.0.0.1:8080/service-a/ping

# Unknown path prefix -> 404
curl -i -H "X-API-Key: demo-key-1" http://127.0.0.1:8080/service-unknown/ping

# Exceed the rate limit -> eventually 429
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}\n" -H "X-API-Key: demo-key-1" \
    http://127.0.0.1:8080/service-a/ping
done
```

## Running the tests

```bash
python -m unittest discover -s tests -v
# or, if you have pytest installed:
pytest tests/ -v
```

The test suite spins up real HTTP servers on ephemeral (`port=0`) localhost
ports for both the gateway and its "upstreams" — no external network access
or already-running services are required. It covers:

- a valid request being proxied to the correct upstream (and the correct
  path/method arriving at that upstream),
- routing to a second, differently-configured upstream,
- an unknown path prefix returning `404`,
- a missing `X-API-Key` header returning `401`,
- an invalid `X-API-Key` value returning `401`,
- the token-bucket rate limiter returning `429` once exhausted, tracked
  independently per API key,
- structured request logging capturing method, path, status, and latency
  for both successful and rejected requests.

## CI

`.github/workflows/tests.yml` runs the full test suite on every push and
pull request against Python 3.11 and 3.12.

## License

MIT — see [LICENSE](LICENSE).
