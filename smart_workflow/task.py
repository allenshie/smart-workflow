"""Task primitives shared by the workflow runner."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .monitoring import MonitoringClient


@dataclass
class TaskResult:
    """Represents the outcome of a task execution."""

    status: str = "success"
    payload: Optional[Dict[str, Any]] = None


class TaskError(RuntimeError):
    """Raised when a task needs to fail gracefully."""


class TaskContext:
    """Holds shared objects (config, monitor, resources)."""

    def __init__(
        self,
        logger: logging.Logger,
        config: Any,
        monitor: MonitoringClient,
        resources: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger = logger
        self.config = config
        self.monitor = monitor
        self._resources: Dict[str, Any] = resources or {}

    def set_resource(self, key: str, value: Any) -> None:
        self._resources[key] = value

    def get_resource(self, key: str) -> Any:
        return self._resources.get(key)

    def require_resource(self, key: str) -> Any:
        if key not in self._resources:
            raise TaskError(f"resource '{key}' not found")
        return self._resources[key]

    def report_success(self, component: str, detail: str | None = None) -> None:
        self.monitor.report_event("success", detail=detail, component=component)

    def report_failure(self, component: str, detail: str | None = None) -> None:
        self.monitor.report_event("failure", detail=detail, component=component)

    def report_disabled(self, component: str, detail: str | None = None) -> None:
        self.monitor.report_event("disabled", detail=detail, component=component)


class BaseTask:
    """BaseTask wraps monitoring hooks around ``run``."""

    name = "task"

    def execute(self, context: TaskContext) -> TaskResult:
        context.logger.info("開始任務：%s", self.name)
        try:
            result = self.run(context)
        except TaskError as exc:
            context.report_failure(self.name, detail=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            context.report_failure(self.name, detail=str(exc))
            raise

        result = result or TaskResult()
        context.report_success(self.name)
        return result

    def run(self, context: TaskContext) -> TaskResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self, context: TaskContext) -> None:
        """Lifecycle hook invoked by WorkflowRunner during shutdown."""
        return None


__all__ = [
    "TaskResult",
    "TaskError",
    "TaskContext",
    "BaseTask",
]
