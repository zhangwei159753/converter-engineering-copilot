"""Background task lifecycle orchestration over SQLite and LangGraph."""

import asyncio
import logging
from collections.abc import Callable
from uuid import uuid4

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.config import WorkerSettings
from converter_copilot.domain.schemas import (
    ConverterReport,
    TaskCreate,
    TaskMode,
    TaskRecord,
)
from converter_copilot.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


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
        *,
        settings: WorkerSettings | None = None,
    ):
        """Bind a durable task store to a workflow dependency provider."""
        self.store = store
        self.deps_provider = deps_provider or self._default_deps
        self.settings = settings
        self._worker_id = f"worker-{uuid4()}"
        self._worker_task: asyncio.Task | None = None
        self._active: dict[str, asyncio.Task] = {}

    def _get_settings(self) -> WorkerSettings:
        if self.settings is None:
            self.settings = WorkerSettings.from_env()
        return self.settings

    @property
    def is_running(self) -> bool:
        """Whether the worker polling task is still alive."""
        return self._worker_task is not None and not self._worker_task.done()

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
        record, reused = self.store.create_task(request, idempotency_key=idempotency_key)
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
        retry, reused = self.store.create_retry(
            task_id, request, idempotency_key=idempotency_key, max_retries=self.MAX_RETRIES
        )
        return retry, reused

    async def start(self) -> None:
        """Recover expired leases and start the bounded polling worker."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._get_settings()
        self.store.requeue_expired_tasks()
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        """Stop polling and cancel local executions so their leases can recover."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        active = list(self._active.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def run_once(self) -> bool:
        """Claim and execute one queued task; return whether a task was claimed."""
        settings = self._get_settings()
        record = self.store.claim_next_task(
            self._worker_id,
            lease_seconds=settings.lease_seconds,
            max_attempts=settings.max_attempts,
        )
        if record is None:
            return False
        await self._execute_claimed(record, settings)
        return True

    async def _worker_loop(self) -> None:
        settings = self._get_settings()
        while True:
            while len(self._active) < settings.concurrency:
                record = self.store.claim_next_task(
                    self._worker_id,
                    lease_seconds=settings.lease_seconds,
                    max_attempts=settings.max_attempts,
                )
                if record is None:
                    break
                self._active[record.task_id] = asyncio.create_task(self._execute_claimed(record, settings))
            await asyncio.sleep(settings.poll_seconds)

    async def _execute_claimed(self, record: TaskRecord, settings: WorkerSettings) -> None:
        task_id = record.task_id
        try:
            deps = self.deps_provider()
            deps.case_store = self.store
            graph = build_workflow(record.mode, deps)
            work = asyncio.create_task(
                asyncio.wait_for(
                    graph.ainvoke({"task_id": task_id, "inputs": record.inputs}),
                    timeout=settings.timeout_seconds,
                )
            )
            while not work.done():
                await asyncio.wait({work}, timeout=max(settings.lease_seconds / 3, 0.05))
                if work.done():
                    break
                renewed = self.store.renew_task_lease(
                    task_id, self._worker_id, lease_seconds=settings.lease_seconds
                )
                if not renewed:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    return
            state = await work
            report: ConverterReport | None = state.get("report")
            if report is None:
                raise RuntimeError("Workflow completed without a report.")
            self.store.finish_task(task_id, report, worker_id=self._worker_id)
        except asyncio.TimeoutError:
            self.store.fail_task(task_id, worker_id=self._worker_id, error_code="task_timeout")
        except Exception:
            logger.exception("Task execution failed: %s", task_id)
            self.store.fail_task(task_id, worker_id=self._worker_id, error_code="workflow_error")
        finally:
            current = self._active.get(task_id)
            if current is asyncio.current_task():
                self._active.pop(task_id, None)
