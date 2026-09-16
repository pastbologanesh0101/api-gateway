"""Configuration objects for the API gateway."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Route:
    """Maps a path prefix to an upstream base URL.

    e.g. Route(prefix="/service-a", upstream="http://127.0.0.1:9001")
    means a request to /service-a/foo is proxied to
    http://127.0.0.1:9001/foo
    """

    prefix: str
    upstream: str

    def __post_init__(self) -> None:
        if not self.prefix.startswith("/"):
            raise ValueError("prefix must start with '/'")
        # Normalize: no trailing slash on prefix (except root "/")
        object.__setattr__(self, "prefix", self.prefix.rstrip("/") or "/")
        object.__setattr__(self, "upstream", self.upstream.rstrip("/"))


@dataclass
class GatewayConfig:
    """Full gateway configuration."""

    routes: list[Route] = field(default_factory=list)
    api_keys: set[str] = field(default_factory=set)
    rate_limit_capacity: int = 5
    rate_limit_refill_per_sec: float = 1.0
    request_timeout: float = 5.0
    log_file: str | None = None  # None => stdout

    def match_route(self, path: str) -> tuple[Route, str] | None:
        """Find the route whose prefix matches ``path``.

        Returns (route, remainder) where remainder is the part of the path
        after the prefix (always starting with '/'), or None if no route
        matches. Longest-prefix match wins.
        """
        best: Route | None = None
        for route in self.routes:
            prefix = route.prefix
            if prefix == "/":
                matches = True
            else:
                matches = path == prefix or path.startswith(prefix + "/")
            if matches and (best is None or len(route.prefix) > len(best.prefix)):
                best = route

        if best is None:
            return None

        if best.prefix == "/":
            remainder = path
        else:
            remainder = path[len(best.prefix):] or "/"
        if not remainder.startswith("/"):
            remainder = "/" + remainder
        return best, remainder
