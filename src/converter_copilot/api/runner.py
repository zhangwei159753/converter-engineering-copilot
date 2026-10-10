"""Background task lifecycle orchestration over SQLite and LangGraph."""

import asyncio
from collections.abc import Callable

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.domain.schemas import ConverterReport, TaskCreate, TaskMode, TaskRecord
from converter_copilot.storage.sqlite_store import SQLiteStore


class WorkflowUnavailableError(RuntimeError):
    """Raised when a valid mode has not yet been implemented."""


class RetryLimitError(RuntimeError):
    """Raised when a task has exhausted its bounded retry budget."""


class TaskRunner:
    """Run one background graph per persisted task, with durable events."""

    MAX_RETRIES = 2
    _AVAILABLE_MODES = {TaskMode.FAULT_ANALYSIS}

    def __init__(
        self,
        store: SQLiteStore,
        deps_provider: Callable[[], WorkflowDeps] | None = None,
    ):
        self.store = store
        self.deps_provider = deps_provider or self._default_deps
        self._active: dict[str, asyncio.Task] = {}

    @staticmethod
    def _default_deps() -> WorkflowDeps:
        from converter_copilot.agent.entrypoint import build_default_deps

        return build_default_deps()

    async def create(self, request: TaskCreate) -> TaskRecord:
        """Validate availability, persist a queued task, then schedule one run."""
        if request.mode not in self._AVAILABLE_MODES:
            raise WorkflowUnavailableError(f"The {request.mode.value} workflow is not available yet.")
        graph = build_workflow(request.mode, self.deps_provider())
        record = self.store.create_task(request)
        self.store.append_event(record.task_id, stage="queued", status="queued", message="任务已排队。")
        self._active[record.task_id] = asyncio.create_task(self._execute(record, graph))
        return record

    async def retry(self, task_id: str) -> TaskRecord:
        """Create a new bounded attempt for a failed or partial task."""
        original = self.store.get_task(task_id)
        if original is None:
            raise KeyError(task_id)
        if original.status not in {"failed", "partial"}:
            raise ValueError("Only failed or partial tasks can be retried.")
        if self.store.retry_count(task_id) >= self.MAX_RETRIES:
            raise RetryLimitError(f"Task {task_id} has reached the retry limit.")
        retry = await self.create(TaskCreate(mode=original.mode, inputs=original.inputs))
        self.store.record_retry(task_id, retry.task_id)
        self.store.append_event(retry.task_id, stage="retry", status="queued", message=f"重试任务，来源任务 {task_id}。")
        return retry

    async def _execute(self, record: TaskRecord, graph) -> None:
        task_id = record.task_id
        self.store.update_task_status(task_id, "running")
        self.store.append_event(task_id, stage="workflow", status="running", message="工作流开始执行。")
        try:
            state = await graph.ainvoke({"task_id": task_id, "inputs": record.inputs})
            report: ConverterReport | None = state.get("report")
            if report is None:
                raise RuntimeError("Workflow completed without a report.")
            self.store.save_report(report)
            self.store.update_task_status(task_id, report.completion_status)
            message = "报告已生成。" if report.completion_status == "completed" else "已生成部分报告，请检查提示和证据。"
            self.store.append_event(task_id, stage="complete", status=report.completion_status, message=message)
        except Exception as error:
            self.store.update_task_status(task_id, "failed")
            self.store.append_event(
                task_id,
                stage="failed",
                status="failed",
                message=f"工作流执行失败（{type(error).__name__}）。可查看服务日志并重试。",
            )
        finally:
            self._active.pop(task_id, None)
