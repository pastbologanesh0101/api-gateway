"""A minimal thread-safe token-bucket rate limiter.

Each client (identified by an arbitrary string key, e.g. an API key or IP
address) gets its own bucket. The bucket holds up to ``capacity`` tokens and
refills at ``refill_rate`` tokens per second. Every allowed request consumes
one token.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketRateLimiter:
    """Per-client token-bucket rate limiter.

    Parameters
    ----------
    capacity:
        Maximum number of tokens (i.e. burst size) a bucket can hold.
    refill_rate:
        Tokens added per second.
    """

    def __init__(self, capacity: int = 5, refill_rate: float = 1.0) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, client_key: str, now: float) -> _Bucket:
        bucket = self._buckets.get(client_key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
            self._buckets[client_key] = bucket
        return bucket

    def allow(self, client_key: str) -> bool:
        """Return True if a request from ``client_key`` should be allowed.

        Consumes one token if allowed.
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._get_bucket(client_key, now)
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                self.capacity, bucket.tokens + elapsed * self.refill_rate
            )
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def reset(self) -> None:
        """Clear all bucket state. Mainly useful for tests."""
        with self._lock:
            self._buckets.clear()
