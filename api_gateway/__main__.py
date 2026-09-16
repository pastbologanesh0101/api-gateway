"""CLI entry point: run the gateway with a demo configuration.

Usage:
    python -m api_gateway [--port PORT]

This starts the gateway plus two in-process demo upstreams (service-a,
service-b) so you can try it immediately with curl. See README.md for
details and example requests.
"""

from __future__ import annotations

import argparse
import threading

from .config import GatewayConfig, Route
from .gateway import build_server
from .testing_upstream import build_upstream


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the API Gateway demo.")
    parser.add_argument("--port", type=int, default=8080, help="Gateway port")
    parser.add_argument(
        "--api-key",
        action="append",
        default=None,
        help="Valid API key (may be passed multiple times). Default: demo-key-1, demo-key-2",
    )
    args = parser.parse_args()

    api_keys = set(args.api_key) if args.api_key else {"demo-key-1", "demo-key-2"}

    upstream_a = build_upstream("service-a")
    upstream_b = build_upstream("service-b")
    threading.Thread(target=upstream_a.serve_forever, daemon=True).start()
    threading.Thread(target=upstream_b.serve_forever, daemon=True).start()

    a_port = upstream_a.server_address[1]
    b_port = upstream_b.server_address[1]

    config = GatewayConfig(
        routes=[
            Route(prefix="/service-a", upstream=f"http://127.0.0.1:{a_port}"),
            Route(prefix="/service-b", upstream=f"http://127.0.0.1:{b_port}"),
        ],
        api_keys=api_keys,
        rate_limit_capacity=10,
        rate_limit_refill_per_sec=5.0,
    )

    server = build_server(config, port=args.port)
    print(
        f"API Gateway listening on http://127.0.0.1:{server.server_address[1]}\n"
        f"  demo upstreams: service-a -> :{a_port}, service-b -> :{b_port}\n"
        f"  valid API keys: {', '.join(sorted(api_keys))}\n"
        "Try:\n"
        f"  curl -H 'X-API-Key: demo-key-1' http://127.0.0.1:{server.server_address[1]}/service-a/ping"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        upstream_a.shutdown()
        upstream_b.shutdown()


if __name__ == "__main__":
    main()
