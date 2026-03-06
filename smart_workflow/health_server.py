"""Minimal HTTP probe server for workflow health checks."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Dict, Tuple

from .health import HealthSnapshot, HealthState, ProbeConfig


class HealthServer:
    """Expose startup/liveness/readiness endpoints backed by HealthState."""

    def __init__(
        self,
        health_state: HealthState,
        host: str = "0.0.0.0",
        port: int = 8081,
        probe_config: ProbeConfig | None = None,
    ) -> None:
        self._health_state = health_state
        self._host = host
        self._port = port
        self._probe_config = probe_config or ProbeConfig()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return

        health_state = self._health_state
        probe_config = self._probe_config

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/startupz", "/healthz", "/readyz"}:
                    self._send_json(404, {"status": "not_found", "path": self.path})
                    return

                snapshot = health_state.snapshot()
                now = time.time()

                if self.path == "/startupz":
                    ok, checks = _evaluate_startup(snapshot)
                    code = 200 if ok else 503
                    self._send_json(code, _build_payload("startup", ok, checks, snapshot, now))
                    return

                if self.path == "/healthz":
                    ok, checks = _evaluate_liveness(snapshot, now, probe_config)
                    code = 200 if ok else 503
                    self._send_json(code, _build_payload("liveness", ok, checks, snapshot, now))
                    return

                ok, checks = _evaluate_readiness(snapshot, now, probe_config)
                code = 200 if ok else 503
                self._send_json(code, _build_payload("readiness", ok, checks, snapshot, now))

            def log_message(self, _format: str, *args: Any) -> None:
                _ = args
                return

            def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._thread = Thread(target=self._httpd.serve_forever, name="health-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None


def _evaluate_startup(snapshot: HealthSnapshot) -> Tuple[bool, Dict[str, bool]]:
    checks = {"startup_ok": snapshot.startup_ok}
    return checks["startup_ok"], checks


def _evaluate_liveness(
    snapshot: HealthSnapshot,
    now: float,
    config: ProbeConfig,
) -> Tuple[bool, Dict[str, bool]]:
    loop_recent = False
    if snapshot.last_loop_ts is not None:
        loop_recent = (now - snapshot.last_loop_ts) <= config.liveness_timeout_seconds
    elif snapshot.startup_ok and snapshot.startup_done_ts is not None:
        # Allow a short grace period between startup completion and first loop tick.
        loop_recent = (now - snapshot.startup_done_ts) <= config.startup_grace_seconds

    checks = {
        "startup_ok": snapshot.startup_ok,
        "not_stopping": not snapshot.stopping,
        "loop_recent": loop_recent,
    }
    return all(checks.values()), checks


def _evaluate_readiness(
    snapshot: HealthSnapshot,
    now: float,
    config: ProbeConfig,
) -> Tuple[bool, Dict[str, bool]]:
    progress_recent = False
    if snapshot.last_progress_ts is not None:
        progress_recent = (now - snapshot.last_progress_ts) <= config.readiness_timeout_seconds
    elif snapshot.startup_ok and snapshot.startup_done_ts is not None:
        progress_recent = (now - snapshot.startup_done_ts) <= config.startup_grace_seconds

    checks = {
        "startup_ok": snapshot.startup_ok,
        "not_stopping": not snapshot.stopping,
        "not_in_backoff": not snapshot.in_backoff,
        "progress_recent": progress_recent,
    }
    return all(checks.values()), checks


def _build_payload(
    probe_type: str,
    ok: bool,
    checks: Dict[str, bool],
    snapshot: HealthSnapshot,
    now: float,
) -> Dict[str, Any]:
    return {
        "probe": probe_type,
        "status": "ok" if ok else "fail",
        "timestamp": now,
        "checks": checks,
        "state": snapshot.to_dict(),
    }


__all__ = ["HealthServer"]
