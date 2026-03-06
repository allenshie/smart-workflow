"""Health state primitives for workflow runtime probes."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass
class ProbeConfig:
    """Timeout knobs for startup/liveness/readiness probe checks."""

    liveness_timeout_seconds: float = 30.0
    readiness_timeout_seconds: float = 30.0
    startup_grace_seconds: float = 10.0


@dataclass
class HealthSnapshot:
    """Immutable view of workflow health state."""

    started_ts: float
    startup_ok: bool
    startup_done_ts: float | None
    last_loop_ts: float | None
    last_progress_ts: float | None
    in_backoff: bool
    last_error: str | None
    last_error_ts: float | None
    stopping: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HealthState:
    """Thread-safe mutable state shared by runner and probe server."""

    def __init__(self) -> None:
        now = time.time()
        self._lock = threading.Lock()
        self._started_ts = now
        self._startup_ok = False
        self._startup_done_ts: float | None = None
        self._last_loop_ts: float | None = None
        self._last_progress_ts: float | None = None
        self._in_backoff = False
        self._last_error: str | None = None
        self._last_error_ts: float | None = None
        self._stopping = False

    def mark_startup_ok(self) -> None:
        with self._lock:
            self._startup_ok = True
            self._startup_done_ts = time.time()

    def mark_loop_tick(self) -> None:
        with self._lock:
            self._last_loop_ts = time.time()

    def mark_progress(self) -> None:
        with self._lock:
            self._last_progress_ts = time.time()
            self._last_error = None
            self._last_error_ts = None

    def mark_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._last_error_ts = time.time()

    def set_backoff(self, value: bool) -> None:
        with self._lock:
            self._in_backoff = value

    def mark_stopping(self) -> None:
        with self._lock:
            self._stopping = True

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            return HealthSnapshot(
                started_ts=self._started_ts,
                startup_ok=self._startup_ok,
                startup_done_ts=self._startup_done_ts,
                last_loop_ts=self._last_loop_ts,
                last_progress_ts=self._last_progress_ts,
                in_backoff=self._in_backoff,
                last_error=self._last_error,
                last_error_ts=self._last_error_ts,
                stopping=self._stopping,
            )


__all__ = [
    "HealthState",
    "HealthSnapshot",
    "ProbeConfig",
]
