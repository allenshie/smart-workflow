"""Monitoring client responsible for sending heartbeats and events."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict

LOGGER = logging.getLogger(__name__)


@dataclass
class MonitoringClient:
    """Simple HTTP client for smart-workflow monitoring integration.

    ``monitor_endpoint`` 可以是 monitoring server 的 base URL（如 ``http://host:9400``），
    也可以是完整的 ``/events`` endpoint。當 base URL 提供時，本類別會自動推導
    ``/events`` 與 ``/heartbeat`` 兩個路徑。
    """

    monitor_endpoint: str | None = None
    service_name: str = "service"
    timeout_seconds: float = 3.0
    _events_url: str | None = field(init=False, repr=False, default=None)
    _heartbeat_url: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if not self.monitor_endpoint:
            return

        base = self.monitor_endpoint.rstrip("/")
        if base.endswith("/events") or base.endswith("/heartbeat"):
            base = base.rsplit("/", 1)[0]

        self._events_url = f"{base}/events"
        self._heartbeat_url = f"{base}/heartbeat"

    def heartbeat(self, phase: str | None = None) -> None:
        if not self._heartbeat_url:
            LOGGER.debug("monitor heartbeat skipped (no endpoint configured)")
            return

        payload: Dict[str, Any] = {"service": self.service_name}
        if phase:
            payload["phase"] = phase
        self._post(self._heartbeat_url, payload, log_level=logging.DEBUG)

    def report_event(self, event_type: str, detail: str | None = None, **extra: Any) -> None:
        if not self._events_url:
            LOGGER.debug("monitor event skipped (no endpoint configured): %s", event_type)
            return

        payload: Dict[str, Any] = {
            "service": self.service_name,
            "event_type": event_type,
        }
        if detail is not None:
            payload["detail"] = detail
        if extra:
            payload.update(extra)
        self._post(self._events_url, payload)

    def _post(self, url: str, payload: Dict[str, Any], log_level: int = logging.WARNING) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds):
                return
        except urllib.error.URLError as exc:  # pragma: no cover - network errors
            LOGGER.log(log_level, "monitoring call failed (%s): %s", url, exc)


__all__ = ["MonitoringClient"]
