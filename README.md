# smart-workflow

輕量級的 Python workflow orchestrator，直接取自 /common 專案的實務設計：
TaskContext 與 BaseTask 封裝監控回報、WorkflowRunner 管理啟動流程與常駐 while-loop，並提供心跳／事件上報給 monitoring server。

## 功能
- **TaskContext / BaseTask**：與 monitoring client 深度整合，統一 success / failure / disabled 回報，並支援共享資源。
- **Workflow + Runner**：啟動任務先跑一次，再執行單一 loop 任務，支援固定 sleep 與 retry backoff 秒數。
- **MonitoringClient**：簡單的 HTTP heartbeat / events client，可直接放入 TaskContext 以通報 pipeline 狀態。
- **Health Probes（可選）**：提供 `HealthState`、`HealthAwareWorkflowRunner`、`HealthServer`（`http.server`）以支援 `/startupz`、`/healthz`、`/readyz`。
- **範例**：`smart_workflow/examples/simple_pipeline.py` 展示完整 wiring。

## 安裝

### 使用 pip 安裝
> ⚠️ 請先確認安裝器版本 `pip >= 23`、`setuptools >= 61`，舊版（常見於 arm / CUDA 裝置）在解析 `pyproject.toml` 時會產生 `UNKNOWN.egg-info`。
> 若版本不足，可透過 `python -m pip install --upgrade "pip>=23" "setuptools>=61" "wheel"` 更新後再安裝。

```bash
pip install git+https://github.com/allenshie/smart-workflow.git
```
或在 mono-repo 內透過 `pip install -e /path/to/smart-workflow` 使用。

### 使用 uv 安裝
```bash
uv pip install git+https://github.com/allenshie/smart-workflow.git
uv pip install -e /path/to/smart-workflow
```

### 本地開發快速體驗
```bash
cd /path/to/smart-workflow
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install .          # 或 pip install -e .（需 pip >= 23.1）
python smart_workflow/examples/simple_pipeline.py
```
> Windows 使用 PowerShell 時可改用 `.venv\Scripts\Activate.ps1`。

若習慣使用 [uv](https://github.com/astral-sh/uv)：
```bash
cd /path/to/smart-workflow
uv venv
source .venv/bin/activate
uv pip install -e .
python smart_workflow/examples/simple_pipeline.py
```

## 快速上手（完整流程）
下列流程對應實務專案的 main 函數：

1. **準備 Logger / Config / MonitoringClient**：Logger 採 `logging.Logger`，Config 建議用 `dataclass` 定義，MonitoringClient 需要從 Config 取得 endpoint、service name。
2. **建立 TaskContext**：`context = TaskContext(logger=logger, config=config, monitor=monitor)`。
3. **註冊共享資源**：`context.set_resource("scheduler", scheduler)` 等後續任務需要的物件。
4. **建立 Workflow 與任務類**：繼承 `BaseTask` 撰寫 `InitPipelineTask`（暖機）與 `PipelineScheduler`（loop）。若 pipeline 內含多個節點，可在 `InitPipelineTask` 內從 `context.config` 取得參數，並在此就實例化各個 node（如 `IngestionTask(source_cfg)`、`InferenceTask(weight_path)`、`PublishTask(endpoint)`），避免 loop 時反覆建立昂貴資源。
5. **加入啟動任務**：`workflow.add_startup_task("pipeline-task-init", lambda: InitPipelineTask())`，適合做一次性的初始化，例如模型載入或 pipeline node 實例化。
6. **設定 loop 任務**：`workflow.set_loop("pipeline-scheduler", lambda: PipelineScheduler())`（loop 任務負責取 resource 並 repeat 執行 pipeline）。
7. **實例化 Runner**：
   ```python
   runner = WorkflowRunner(
       context=context,
       workflow=workflow,
       loop_interval=config.loop_interval_seconds,
       retry_backoff=config.retry_backoff_seconds,
   )
   ```
8. **執行 `runner.run()`**：Runner 會依序完成上述任務並常駐執行 loop。

### 範例程式
```python
import logging
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


@dataclass
class AppConfig:
    monitor_endpoint: str
    service_name: str = "demo"
    loop_interval_seconds: float = 1.0
    retry_backoff_seconds: float = 3.0
    pipeline_model_path: str = "./model.bin"


class PipelineNode(BaseTask):
    """每個節點都可以繼承 BaseTask 取得監控回報。"""


class IngestionTask(PipelineNode):
    name = "ingestion"

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name

    def run(self, context: TaskContext) -> TaskResult:
        frame = f"frame-{self.source_name}"
        context.set_resource("current_frame", frame)
        return TaskResult()


class InferenceTask(PipelineNode):
    name = "inference"

    def __init__(self, weight_path: str) -> None:
        self.weight_path = weight_path
        self.model = self._load_weight(weight_path)

    def _load_weight(self, weight_path: str) -> str:
        return f"model-loaded-from-{weight_path}"

    def run(self, context: TaskContext) -> TaskResult:
        frame = context.require_resource("current_frame")
        context.logger.info("inference on %s using %s", frame, self.weight_path)
        if frame.endswith("5"):
            raise TaskError("model timeout")
        context.set_resource("inference_output", {"boxes": 2})
        return TaskResult()


class PublishTask(PipelineNode):
    name = "publish"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def run(self, context: TaskContext) -> TaskResult:
        payload = context.require_resource("inference_output")
        context.logger.info("push result %s to %s", payload, self.endpoint)
        return TaskResult()


class PipelineTask:
    def __init__(
        self,
        *,
        config: AppConfig,
        logger: logging.Logger,
        nodes: list[PipelineNode],
    ) -> None:
        self.config = config
        self.logger = logger
        self.pipeline_nodes = nodes

    def warmup(self) -> None:
        self.logger.info("loading model from %s", self.config.pipeline_model_path)

    def execute(self, context: TaskContext) -> None:
        for node in self.pipeline_nodes:
            node.execute(context)


class InitPipelineTask(BaseTask):
    name = "pipeline-task-init"

    def run(self, context: TaskContext) -> TaskResult:
        ingest = IngestionTask(context.config.service_name)
        infer = InferenceTask(context.config.pipeline_model_path)
        publish = PublishTask("http://downstream/api")
        pipeline = PipelineTask(config=context.config, logger=context.logger, nodes=[ingest, infer, publish])
        pipeline.warmup()
        context.set_resource("pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    name = "pipeline-scheduler"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline: PipelineTask = context.require_resource("pipeline")
        context.monitor.heartbeat(phase="pipeline-loop")
        pipeline.execute(context)
        return TaskResult(payload={"sleep": context.config.loop_interval_seconds})


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("demo")
    config = AppConfig(monitor_endpoint="http://localhost:9400", service_name="demo")
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
        logger.info("workflow stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 補充說明
- `InitPipelineTask` 負責 pipeline 初始化與 `context.set_resource("pipeline", pipeline)`；`PipelineScheduler` 透過 `context.require_resource` 取得同一個 pipeline 實例，確保 while-loop 可重複利用。
- `TaskResult.payload` 可帶入 `{"sleep": 秒數}` 覆寫下一輪 loop 的 sleep，若沒有則使用 `loop_interval`。
- 任何繼承 `BaseTask` 的任務只要丟出 `TaskError` 就會記錄可預期錯誤並啟動 retry backoff；其他例外視為未知錯誤，也會走相同 backoff。
- `context.monitor.heartbeat()` 與 `context.report_success()` 等函式最後都會呼叫 `MonitoringClient` 的 HTTP API，方便串接既有 monitoring server。

## K8s Probe Integration (Optional)

若服務要被 Kubernetes 的 `startupProbe` / `livenessProbe` / `readinessProbe` 監控，
可改用 `HealthAwareWorkflowRunner` 並啟動 `HealthServer`：

```python
from smart_workflow import (
    HealthAwareWorkflowRunner,
    HealthServer,
    HealthState,
    Workflow,
)

health_state = HealthState()
health_server = HealthServer(health_state, host="0.0.0.0", port=8081)
health_server.start()

workflow = Workflow()
# ... add startup / loop tasks

runner = HealthAwareWorkflowRunner(
    context=context,
    workflow=workflow,
    loop_interval=5.0,
    retry_backoff=5.0,
    health_state=health_state,
)

try:
    runner.run()
finally:
    health_server.stop()
```

探針語意：
- `/startupz`：startup 任務是否完成。
- `/healthz`：loop 心跳是否在 `liveness_timeout_seconds` 內。
- `/readyz`：startup 完成、最近有進度且不在 backoff（可調整規則）。

如需調整 timeout，可透過 `ProbeConfig` 傳入 `HealthServer`。

歡迎 issue / PR！
