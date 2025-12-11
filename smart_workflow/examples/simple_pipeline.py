"""Simple demonstration of the workflow runner."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from smart_workflow import (
    BaseTask,
    MonitoringClient,
    TaskContext,
    TaskError,
    TaskResult,
    Workflow,
    WorkflowRunner,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class DemoConfig:
    monitor_endpoint: str = "http://localhost:9400"
    service_name: str = "demo"
    loop_interval_seconds: float = 1.0
    retry_backoff_seconds: float = 3.0
    pipeline_model_path: str = "./model.bin"


class DemoPipeline:
    def __init__(self, config: DemoConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.counter = 0

    def warmup(self) -> None:
        self.logger.info("loading pipeline artifact from %s", self.config.pipeline_model_path)

    def execute(self) -> None:
        self.counter += 1
        if self.counter % 4 == 0:
            raise TaskError("simulated pipeline failure")
        self.logger.info("processed demo batch #%d", self.counter)


class InitPipelineTask(BaseTask):
    name = "pipeline-task-init"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline = DemoPipeline(context.config, context.logger)
        pipeline.warmup()
        context.set_resource("pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    name = "pipeline-scheduler"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline = context.require_resource("pipeline")
        context.monitor.heartbeat(phase="pipeline-loop")
        pipeline.execute()
        return TaskResult(payload={"sleep": 1.5})


def main() -> int:
    logger = logging.getLogger("workflow-demo")
    config = DemoConfig()
    monitor = MonitoringClient(
        monitor_endpoint=config.monitor_endpoint,
        service_name=config.service_name,
    )

    context = TaskContext(logger=logger, config=config, monitor=monitor)

    workflow = Workflow()
    workflow.add_startup_task("pipeline-task-init", lambda: InitPipelineTask())
    workflow.set_loop("pipeline-scheduler", lambda: PipelineScheduler())

    runner = WorkflowRunner(
        context=context,
        workflow=workflow,
        loop_interval=config.loop_interval_seconds,
        retry_backoff=config.retry_backoff_seconds,
    )

    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
