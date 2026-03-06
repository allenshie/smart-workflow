"""Workflow orchestration primitives."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .health import HealthState
from .task import BaseTask, TaskContext, TaskError, TaskResult

TaskFactory = Callable[[], BaseTask]


@dataclass
class WorkflowStartupTask:
    name: str | None
    factory: TaskFactory


@dataclass
class WorkflowLoopTask:
    name: str | None
    factory: TaskFactory


class Workflow:
    """Defines startup tasks and the looping task factory."""

    def __init__(self) -> None:
        self._startup_tasks: List[WorkflowStartupTask] = []
        self._loop_task: Optional[WorkflowLoopTask] = None

    def add_startup_task(
        self,
        factory_or_name: TaskFactory | str,
        factory: TaskFactory | None = None,
    ) -> None:
        name: str | None
        task_factory: TaskFactory
        if callable(factory_or_name) and factory is None:
            name = None
            task_factory = factory_or_name
        elif isinstance(factory_or_name, str) and factory is not None and callable(factory):
            name = factory_or_name
            task_factory = factory
        else:
            raise TypeError("add_startup_task expects a factory or (name, factory)")

        self._startup_tasks.append(WorkflowStartupTask(name=name, factory=task_factory))

    def set_loop(
        self,
        factory_or_name: TaskFactory | str,
        factory: TaskFactory | None = None,
    ) -> None:
        name: str | None
        task_factory: TaskFactory
        if callable(factory_or_name) and factory is None:
            name = None
            task_factory = factory_or_name
        elif isinstance(factory_or_name, str) and factory is not None and callable(factory):
            name = factory_or_name
            task_factory = factory
        else:
            raise TypeError("set_loop expects a factory or (name, factory)")

        self._loop_task = WorkflowLoopTask(name=name, factory=task_factory)

    @property
    def startup_tasks(self) -> List[WorkflowStartupTask]:
        return list(self._startup_tasks)

    @property
    def loop_task(self) -> WorkflowLoopTask:
        if self._loop_task is None:
            raise RuntimeError("Loop task not configured")
        return self._loop_task


class WorkflowRunner:
    """Run workflow startup tasks then keep executing loop tasks."""

    def __init__(
        self,
        context: TaskContext,
        workflow: Workflow,
        loop_interval: float,
        retry_backoff: float,
    ) -> None:
        self.context = context
        self.workflow = workflow
        self.loop_interval = loop_interval
        self.retry_backoff = retry_backoff
        self._startup_task_instances: List[BaseTask] = []
        self._loop_task_instance: BaseTask | None = None

    def run(self) -> None:
        try:
            self._run_startup()
            loop_spec = self.workflow.loop_task
            self._loop_task_instance = loop_spec.factory()
            self.context.logger.info("workflow runner started")

            while True:
                try:
                    if loop_spec.name:
                        self.context.logger.debug("running loop task %s", loop_spec.name)
                    result = self._loop_task_instance.execute(self.context)
                    payload = result.payload or {}
                    sleep_time = payload.get("sleep")
                    time.sleep(float(sleep_time or self.loop_interval))
                except KeyboardInterrupt:
                    self.context.logger.info("workflow runner interrupted")
                    break
                except TaskError as exc:
                    self.context.logger.warning("task error: %s; applying retry backoff", exc)
                    time.sleep(self.retry_backoff)
                except Exception:  # noqa: BLE001
                    self.context.logger.exception("unexpected error; applying retry backoff")
                    time.sleep(self.retry_backoff)
        finally:
            self._shutdown_tasks()

    def _run_startup(self) -> None:
        for spec in self.workflow.startup_tasks:
            if spec.name:
                self.context.logger.info("running startup task %s", spec.name)
            task = spec.factory()
            self._startup_task_instances.append(task)
            task.execute(self.context)

    def _shutdown_tasks(self) -> None:
        tasks: List[BaseTask] = []
        if self._loop_task_instance is not None:
            tasks.append(self._loop_task_instance)
        tasks.extend(reversed(self._startup_task_instances))

        for task in tasks:
            try:
                task.close(self.context)
            except Exception:  # noqa: BLE001
                task_name = getattr(task, "name", task.__class__.__name__)
                self.context.logger.exception("failed to close task: %s", task_name)


class HealthAwareWorkflowRunner(WorkflowRunner):
    """WorkflowRunner with health-state updates for probe endpoints."""

    def __init__(
        self,
        context: TaskContext,
        workflow: Workflow,
        loop_interval: float,
        retry_backoff: float,
        health_state: HealthState,
    ) -> None:
        super().__init__(
            context=context,
            workflow=workflow,
            loop_interval=loop_interval,
            retry_backoff=retry_backoff,
        )
        self.health_state = health_state

    def run(self) -> None:
        try:
            self._run_startup()
            self.health_state.mark_startup_ok()
            loop_spec = self.workflow.loop_task
            self._loop_task_instance = loop_spec.factory()
            self.context.logger.info("workflow runner started")

            while True:
                self.health_state.mark_loop_tick()
                try:
                    if loop_spec.name:
                        self.context.logger.debug("running loop task %s", loop_spec.name)
                    result = self._loop_task_instance.execute(self.context)
                    self.health_state.mark_progress()
                    payload = result.payload or {}
                    sleep_time = payload.get("sleep")
                    time.sleep(float(sleep_time or self.loop_interval))
                except KeyboardInterrupt:
                    self.context.logger.info("workflow runner interrupted")
                    break
                except TaskError as exc:
                    self.health_state.mark_error(str(exc))
                    self.health_state.set_backoff(True)
                    self.context.logger.warning("task error: %s; applying retry backoff", exc)
                    time.sleep(self.retry_backoff)
                    self.health_state.set_backoff(False)
                except Exception:  # noqa: BLE001
                    self.health_state.mark_error("unexpected_error")
                    self.health_state.set_backoff(True)
                    self.context.logger.exception("unexpected error; applying retry backoff")
                    time.sleep(self.retry_backoff)
                    self.health_state.set_backoff(False)
        finally:
            self.health_state.mark_stopping()
            self._shutdown_tasks()


__all__ = [
    "Workflow",
    "WorkflowRunner",
    "HealthAwareWorkflowRunner",
    "TaskFactory",
    "WorkflowStartupTask",
    "WorkflowLoopTask",
]
