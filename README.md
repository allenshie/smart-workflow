# smart-workflow

輕量級的 Python workflow orchestrator，直接取自 /common 專案的實務設計：
TaskContext 與 BaseTask 封裝監控回報、WorkflowRunner 管理啟動流程與常駐 while-loop，並提供心跳／事件上報給 monitoring server。

## 功能
- **TaskContext / BaseTask**：與 monitoring client 深度整合，統一 success / failure / disabled 回報，並支援共享資源。
- **Workflow + Runner**：啟動任務先跑一次，再執行單一 loop 任務，支援固定 sleep 與 retry backoff 秒數。
- **MonitoringClient**：簡單的 HTTP heartbeat / events client，可直接放入 TaskContext 以通報 pipeline 狀態。
- **範例**：`smart_workflow/examples/simple_pipeline.py` 展示完整 wiring。

## 安裝
```bash
pip install git+https://github.com/<your-org>/smart-workflow.git
```
或在 mono-repo 內透過 `pip install -e /path/to/smart-workflow` 使用。

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
4. **建立 Workflow 與任務類**：繼承 `BaseTask` 撰寫 `InitPipelineTask`（暖機）與 `PipelineScheduler`（loop），`run()` 內可以呼叫 `context.monitor.heartbeat()` 與 pipeline 邏輯。
5. **加入啟動任務**：`workflow.add_startup_task("pipeline-task-init", lambda: InitPipelineTask())`，適合做一次性的初始化，例如模型載入。
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


class PipelineTask:
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.counter = 0

    def warmup(self) -> None:
        self.logger.info("loading model from %s", self.config.pipeline_model_path)

    def execute(self) -> None:
        self.counter += 1
        if self.counter % 5 == 0:
            raise TaskError("simulated pipeline failure")
        self.logger.info("run pipeline job #%d", self.counter)


class InitPipelineTask(BaseTask):
    name = "pipeline-task-init"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline = PipelineTask(context.config, context.logger)
        pipeline.warmup()
        context.set_resource("pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    name = "pipeline-scheduler"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline = context.require_resource("pipeline")
        context.monitor.heartbeat(phase="pipeline-loop")
        pipeline.execute()
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

歡迎 issue / PR！
