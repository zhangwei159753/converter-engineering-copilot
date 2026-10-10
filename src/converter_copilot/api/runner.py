"""Background task lifecycle orchestration over SQLite and LangGraph."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.config import ImportLimits, WorkerSettings
from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    ImportRecord,
    ImportRepairResult,
    ImportStatus,
    TaskCreate,
    TaskMode,
    TaskRecord,
)
from converter_copilot.retrieval.case_extractor import extract_case_records
from converter_copilot.retrieval.import_validation import (
    ImportValidationError,
    validate_upload,
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
        self, request: TaskCreate, *, idempotency_key: str | None = None, work_order_id: str | None = None
    ) -> tuple[TaskRecord, bool]:
        """Validate availability and persist a task for worker discovery."""
        if request.mode not in self._AVAILABLE_MODES:
            raise WorkflowUnavailableError(f"The {request.mode.value} workflow is not available yet.")
        record, reused = self.store.create_task(
            request, idempotency_key=idempotency_key, work_order_id=work_order_id
        )
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
        work_order_id = self.store.get_task_work_order_id(task_id)
        retry, reused = self.store.create_retry(
            task_id, request, idempotency_key=idempotency_key, max_retries=self.MAX_RETRIES,
            work_order_id=work_order_id,
        )
        return retry, reused

    async def start(self) -> None:
        """Recover expired leases and start the bounded polling worker."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._get_settings()
        self.store.requeue_expired_tasks()
        if self.store.list_incomplete_imports():
            await self.reconcile_imports()
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def reconcile_imports(self, import_id: str | None = None) -> list[ImportRepairResult]:
        """Replay incomplete imports idempotently and report safe outcomes."""
        records = (
            [record] if (record := self.store.get_import(import_id)) is not None
            else [] if import_id is not None
            else self.store.list_incomplete_imports()
        )
        results = []
        for record in records:
            if record.status == ImportStatus.DELETING:
                results.append(await self.delete_import(record.import_id))
                continue
            results.append(await self._reconcile_one_import(record))
        return results

    async def _reconcile_one_import(self, record: ImportRecord) -> ImportRepairResult:
        source_path = Path(record.source_path)
        if not source_path.is_file():
            updated = self.store.update_import_status(
                record.import_id, ImportStatus.NEEDS_REPAIR,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
                error_code="source_missing",
            )
            return self._import_repair_result(updated)
        try:
            source = validate_upload(
                record.source_name,
                source_path.read_bytes(),
                ImportLimits.from_env(),
                parser_version=record.parser_version,
                chunker_version=record.chunker_version,
            )
        except ImportValidationError:
            updated = self.store.update_import_status(
                record.import_id, ImportStatus.NEEDS_REPAIR,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
                error_code="source_invalid",
            )
            return self._import_repair_result(updated)
        if source.import_id != record.import_id or source.content_sha256 != record.content_sha256:
            updated = self.store.update_import_status(
                record.import_id, ImportStatus.NEEDS_REPAIR,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
                error_code="source_mismatch",
            )
            return self._import_repair_result(updated)

        try:
            deps = self.deps_provider()
            self.store.save_import_chunks(record.import_id, source.chunks)
            if not record.case_candidates_ready:
                candidates = await self._extract_import_candidates(record, source, deps.model)
                self.store.save_import_case_candidates(record.import_id, candidates)
            self.store.update_import_status(
                record.import_id, ImportStatus.INDEXING,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
            )
            deps.vector_index.add(source.chunks)
            case_count = self.store.promote_import_case_candidates(record.import_id)
            updated = self.store.update_import_status(
                record.import_id, ImportStatus.COMPLETE,
                indexed_chunks=len(source.chunks),
                imported_cases=case_count,
            )
        except Exception:
            logger.error("Import replay failed: import_id=%s error_code=import_replay_failed", record.import_id)
            updated = self.store.update_import_status(
                record.import_id, ImportStatus.FAILED,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
                error_code="import_replay_failed",
            )
        return self._import_repair_result(updated)

    async def _extract_import_candidates(self, record: ImportRecord, source, model) -> list[CaseRecord]:
        """Extract or reconstruct candidates exactly once before vector writes."""
        if record.file_type == "csv":
            cases = []
            for row_number, row in enumerate(source.case_rows, start=2):
                case_id = hashlib.sha256(f"{record.import_id}:{row_number}".encode()).hexdigest()[:24]
                matching_chunks = [chunk.chunk_id for chunk in source.chunks if chunk.locator == f"row {row_number}"]
                cases.append(CaseRecord(
                    case_id=f"case-{case_id}",
                    equipment_type=row.get("equipment_type") or None,
                    symptom=row["symptom"],
                    cause=row.get("cause") or None,
                    validation_result=row.get("validation_result") or None,
                    handling_result=row.get("handling_result") or None,
                    evidence_ids=matching_chunks,
                    source_type=row.get("record_type") or row.get("source_type"),
                ))
            return cases
        cases = []
        for chunk in source.chunks[:8]:
            cases.extend(await extract_case_records(
                chunk.text,
                evidence_id=chunk.chunk_id,
                model=model,
                import_id=record.import_id,
            ))
        return cases

    async def inspect_imports(self) -> list[ImportRepairResult]:
        """Mark complete imports with missing vectors for explicit repair."""
        records = self.store.list_import_records()
        if not records:
            return []
        try:
            vector_index = self.deps_provider().vector_index
        except Exception:
            return [self._import_repair_result(record) for record in records]
        results = []
        for record in records:
            if record.status != ImportStatus.COMPLETE:
                results.append(self._import_repair_result(record))
                continue
            if not Path(record.source_path).is_file():
                record = self.store.update_import_status(
                    record.import_id, ImportStatus.NEEDS_REPAIR,
                    indexed_chunks=record.indexed_chunks,
                    imported_cases=record.imported_cases,
                    error_code="source_missing",
                )
            elif not self.store.import_case_data_consistent(record.import_id):
                record = self.store.update_import_status(
                    record.import_id, ImportStatus.NEEDS_REPAIR,
                    indexed_chunks=record.indexed_chunks,
                    imported_cases=record.imported_cases,
                    error_code="cases_incomplete",
                )
            else:
                chunk_ids = self.store.get_import_chunk_ids(record.import_id)
                if vector_index.contains_chunks(chunk_ids):
                    results.append(self._import_repair_result(record))
                    continue
                record = self.store.update_import_status(
                    record.import_id, ImportStatus.NEEDS_REPAIR,
                    indexed_chunks=record.indexed_chunks,
                    imported_cases=record.imported_cases,
                    error_code="vectors_missing",
                )
            results.append(self._import_repair_result(record))
        return results

    async def delete_import(self, import_id: str) -> ImportRepairResult:
        """Complete deletion safely across SQLite, Chroma, and retained bytes."""
        record = self.store.get_import(import_id)
        if record is None:
            raise KeyError(import_id)
        record = self.store.update_import_status(
            import_id, ImportStatus.DELETING,
            indexed_chunks=record.indexed_chunks,
            imported_cases=record.imported_cases,
        )
        try:
            chunk_ids = self.store.get_import_chunk_ids(import_id)
            if chunk_ids:
                self.deps_provider().vector_index.delete_chunks(chunk_ids)
            self.store.delete_import_case_data(import_id)
            self.store.clear_import_chunk_links(import_id)
            Path(record.source_path).unlink(missing_ok=True)
            updated = self.store.update_import_status(
                import_id, ImportStatus.DELETED, indexed_chunks=0, imported_cases=0
            )
        except Exception:
            logger.error("Import deletion interrupted: import_id=%s error_code=delete_incomplete", import_id)
            updated = self.store.update_import_status(
                import_id, ImportStatus.DELETING,
                indexed_chunks=record.indexed_chunks,
                imported_cases=record.imported_cases,
                error_code="delete_incomplete",
            )
        return self._import_repair_result(updated)

    @staticmethod
    def _import_repair_result(record: ImportRecord) -> ImportRepairResult:
        return ImportRepairResult(
            import_id=record.import_id,
            status=record.status,
            expected_chunk_count=record.expected_chunk_count,
            indexed_chunks=record.indexed_chunks,
            imported_cases=record.imported_cases,
            error_code=record.error_code,
        )

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
            logger.error("Task execution failed: task_id=%s error_code=workflow_error", task_id)
            self.store.fail_task(task_id, worker_id=self._worker_id, error_code="workflow_error")
        finally:
            current = self._active.get(task_id)
            if current is asyncio.current_task():
                self._active.pop(task_id, None)
