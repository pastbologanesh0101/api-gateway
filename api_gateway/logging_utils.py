"""Structured request logging.

Emits one line per request in a simple structured key=value format, e.g.:

    method=GET path=/service-a/ping client=1.2.3.4 status=200 latency_ms=3.42

This is easy to grep and easy to parse, without pulling in a JSON logging
dependency.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass


@dataclass
class RequestLogRecord:
    method: str
    path: str
    client: str
    status: int
    latency_ms: float

    def format(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        return (
            f"ts={ts} method={self.method} path={self.path} "
            f"client={self.client} status={self.status} "
            f"latency_ms={self.latency_ms:.2f}"
        )


class RequestLogger:
    """Writes structured request log lines to stdout or a file, thread-safely."""

    def __init__(self, log_file: str | None = None) -> None:
        self._log_file = log_file
        self._lock = threading.Lock()
        self._records: list[RequestLogRecord] = []
        self._capture = False

    def log(
        self, method: str, path: str, client: str, status: int, latency_ms: float
    ) -> RequestLogRecord:
        record = RequestLogRecord(
            method=method,
            path=path,
            client=client,
            status=status,
            latency_ms=latency_ms,
        )
        line = record.format()
        with self._lock:
            if self._capture:
                self._records.append(record)
            if self._log_file:
                with open(self._log_file, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            else:
                print(line, file=sys.stdout, flush=True)
        return record

    def start_capture(self) -> None:
        """Enable in-memory capture of records (used by tests)."""
        with self._lock:
            self._capture = True
            self._records = []

    def captured(self) -> list[RequestLogRecord]:
        with self._lock:
            return list(self._records)
