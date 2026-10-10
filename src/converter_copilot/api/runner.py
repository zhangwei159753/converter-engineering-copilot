"""Background task lifecycle orchestration over SQLite and LangGraph."""

import asyncio
from collections.abc import Callable

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.domain.schemas import (
    ConverterReport,
    TaskCreate,
    TaskMode,
    TaskRecord,
)
from converter_copilot.storage.sqlite_store import SQLiteStore


class WorkflowUnavailableError(RuntimeError):
    """Raised when a valid mode has not yet been implemented."""


class TaskRunner:
    """Run one background graph per persisted task, with durable events."""

    MAX_RETRIES = 2
    _AVAILABLE_MODES = {TaskMode.FAULT_ANALYSIS, TaskMode.TECHNICAL_RESEARCH, TaskMode.CASE_RETRIEVAL}

    def __init__(
        self,
        store: SQLiteStore,
        deps_provider: Callable[[], WorkflowDeps] | None = None,
    ):
        """Bind a durable task store to a workflow dependency provider."""
        self.store = store
        self.deps_provider = deps_provider or self._default_deps
        self._active: dict[str, asyncio.Task] = {}

    @staticmethod
    def _default_deps() -> WorkflowDeps:
        from converter_copilot.agent.entrypoint import build_default_deps

        return build_default_deps()

    async def create(
        self, request: TaskCreate, *, idempotency_key: str | None = None
    ) -> tuple[TaskRecord, bool]:
        """Validate availability, persist a queued task, then schedule one run."""
        if request.mode not in self._AVAILABLE_MODES:
            raise WorkflowUnavailableError(f"The {request.mode.value} workflow is not available yet.")
        deps = self.deps_provider()
        deps.case_store = self.store
        graph = build_workflow(request.mode, deps)
        record, reused = self.store.create_task(request, idempotency_key=idempotency_key)
        if not reused:
            self._active[record.task_id] = asyncio.create_task(self._execute(record, graph))
        return record, reused

    async def retry(self, task_id: str, *, idempotency_key: str | None = None) -> tuple[TaskRecord, bool]:
        """Create a new bounded attempt for a failed or partial task."""
        original = self.store.get_task(task_id)
        if original is None:
            raise KeyError(task_id)
        if original.status not in {"failed", "partial"}:
            raise ValueError("Only failed or partial tasks can be retried.")
        request = TaskCreate(mode=original.mode, inputs=original.inputs)
        if request.mode not in self._AVAILABLE_MODES:
            raise WorkflowUnavailableError(f"The {request.mode.value} workflow is not available yet.")
        deps = self.deps_provider()
        deps.case_store = self.store
        graph = build_workflow(request.mode, deps)
        retry, reused = self.store.create_retry(
            task_id, request, idempotency_key=idempotency_key, max_retries=self.MAX_RETRIES
        )
        if not reused:
            self._active[retry.task_id] = asyncio.create_task(self._execute(retry, graph))
        return retry, reused

    async def _execute(self, record: TaskRecord, graph) -> None:
        task_id = record.task_id
        worker_id = "inline-runner"
        self.store.update_task_status(task_id, "running", worker_id=worker_id)
        self.store.append_event(task_id, stage="workflow", status="running", message="工作流开始执行。")
        try:
            state = await graph.ainvoke({"task_id": task_id, "inputs": record.inputs})
            report: ConverterReport | None = state.get("report")
            if report is None:
                raise RuntimeError("Workflow completed without a report.")
            self.store.finish_task(task_id, report, worker_id=worker_id)
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
