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


class PipelineNode(BaseTask):
    """Base class for demo pipeline nodes."""


class IngestionTask(PipelineNode):
    name = "ingestion"

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name

    def run(self, context: TaskContext) -> TaskResult:
        frame_id = random.randint(1000, 2000)
        context.set_resource("current_frame", frame_id)
        context.logger.info("[%s] ingested frame %s", self.source_name, frame_id)
        return TaskResult()


class InferenceTask(PipelineNode):
    name = "inference"

    def __init__(self, weight_path: str) -> None:
        self.weight_path = weight_path
        self.model = self._load_weight(weight_path)

    def _load_weight(self, weight_path: str) -> str:
        return f"model<{weight_path}>"

    def run(self, context: TaskContext) -> TaskResult:
        frame_id = context.require_resource("current_frame")
        if frame_id % 4 == 0:
            raise TaskError("GPU inference failed")
        context.set_resource("inference_output", {"frame": frame_id, "boxes": 3})
        context.logger.info("inference done for frame %s with %s", frame_id, self.weight_path)
        return TaskResult()


class PublishTask(PipelineNode):
    name = "publish"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def run(self, context: TaskContext) -> TaskResult:
        output = context.require_resource("inference_output")
        context.logger.info("publish result %s to %s", output, self.endpoint)
        return TaskResult()


class DemoPipeline:
    def __init__(
        self,
        *,
        config: DemoConfig,
        logger: logging.Logger,
        nodes: list[PipelineNode],
    ) -> None:
        self.config = config
        self.logger = logger
        self.pipeline_nodes = nodes

    def warmup(self) -> None:
        self.logger.info("loading pipeline artifact from %s", self.config.pipeline_model_path)

    def execute(self, context: TaskContext) -> None:
        for node in self.pipeline_nodes:
            node.execute(context)


class InitPipelineTask(BaseTask):
    name = "pipeline-task-init"

    def run(self, context: TaskContext) -> TaskResult:
        nodes = [
            IngestionTask(context.config.service_name),
            InferenceTask(context.config.pipeline_model_path),
            PublishTask("http://localhost:9500/ingest"),
        ]
        pipeline = DemoPipeline(config=context.config, logger=context.logger, nodes=nodes)
        pipeline.warmup()
        context.set_resource("pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    name = "pipeline-scheduler"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline = context.require_resource("pipeline")
        context.monitor.heartbeat(phase="pipeline-loop")
        pipeline.execute(context)
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
