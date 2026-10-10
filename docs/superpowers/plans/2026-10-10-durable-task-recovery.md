# Durable Task Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make accepted analysis tasks durable across API restarts, bounded by a single-instance worker lease, and observable through the existing API and Web task page.

**Architecture:** SQLite remains the source of truth. Versioned migrations add request idempotency and lease/attempt fields; a lifespan-managed worker claims queued or expired tasks, executes the existing LangGraph workflow, and commits report plus terminal state atomically. Processing is at-least-once, while report writes and retry relationships are idempotent.

**Tech Stack:** Python 3.10+, FastAPI lifespan, SQLite, asyncio, LangGraph, Pydantic, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-local-reliability-v1-design.md`

## Global Constraints

- Run one API/worker instance against the local SQLite and Chroma directories.
- Do not add public deployment, multi-instance workers, Redis, user accounts, or device write/control APIs.
- Persist task and queued event before returning HTTP 202.
- Task execution is at-least-once; model calls can repeat after a crash.
- Never persist raw API keys, full prompts, uploaded content, or raw exception text in ordinary task events/API responses.
- Preserve existing tasks, events, reports, cases, and retry links during migration.

## Review Focus

- API process exits after task acceptance: prove queued task is picked up on restart.
- Worker exits while a task is running: prove expired lease is requeued once and attempt history remains visible.
- Same idempotency key with a changed request: prove HTTP 409 and no second task.
- Crash/failure near report persistence: prove one report slot and terminal status/event commit together.
- SSE reconnect after recovery: prove replay starts after the supplied event ID and includes the recovery event.

---

### Task 1: Version SQLite schema without losing local data

**Files:**
- Create: `src/converter_copilot/storage/migrations.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Test: `tests/converter_copilot/test_sqlite_store.py`

**Interfaces:**
- Produces `migrate(connection: sqlite3.Connection) -> int`, using `PRAGMA user_version` and ordered, transactional migrations.
- `SQLiteStore(database_path, *, initialize: bool = True)` preserves eager initialization for direct callers; `initialize()` runs the versioned migration sequence and is called explicitly by app lifespan for the default API store.
- Task migration adds nullable `idempotency_key` and `request_hash`, integer `attempt`/`max_attempts`, nullable `lease_owner`/`lease_expires_at`/`error_code`, plus a partial unique index for non-null create keys. Retry idempotency keys are unique in `task_retries`.

- [ ] Add tests `test_empty_database_migrates_to_current_version`, `test_existing_v0_rows_survive_migration`, `test_failed_migration_does_not_advance_user_version`, and `test_backup_failure_prevents_migration`.
- [ ] Run `uv run pytest tests/converter_copilot/test_sqlite_store.py -q`; each new test must fail for the missing migration behavior.
- [ ] Move current table creation into migration version 1, add the task lifecycle columns/tables required by this plan in the next migration, and run each migration in its own transaction. Before upgrading a non-empty database, create a consistent SQLite online backup under `.data/backups/`; abort before schema changes if backup creation fails. Expose an explicit initialization method so the FastAPI lifespan can report migration failure through readiness without starting the worker.
- [ ] Re-run `uv run pytest tests/converter_copilot/test_sqlite_store.py -q`; expected: all store tests pass and old rows remain byte-for-byte readable.
- [ ] Commit as `feat: add versioned sqlite migrations`.

### Task 2: Make task creation, retry, and completion transactional

**Files:**
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/api/runner.py`
- Modify: `src/converter_copilot/api/routes/tasks.py`
- Modify: `src/converter_copilot/api/main.py`
- Modify: `src/converter_copilot/api/models.py`
- Test: `tests/converter_copilot/test_sqlite_store.py`
- Test: `tests/converter_copilot/test_task_api.py`

**Interfaces:**
- `SQLiteStore.create_task(request: TaskCreate, *, idempotency_key: str | None = None) -> tuple[TaskRecord, bool]`; bool is true when an existing matching request was returned.
- `SQLiteStore.create_retry(original_task_id: str, request: TaskCreate, *, idempotency_key: str | None = None) -> tuple[TaskRecord, bool]` atomically inserts task, retry link, and queued event; a repeated key returns the same retry task.
- `SQLiteStore.finish_task(task_id: str, report: ConverterReport, *, worker_id: str) -> bool` atomically upserts the report/status/event only while the caller still owns the current lease.
- `TaskRunner.create(request: TaskCreate, *, idempotency_key: str | None = None) -> tuple[TaskRecord, bool]`.

- [ ] Add tests for same-key/same-request reuse, same-key/different-request conflict, repeated retry-key reuse, atomic retry-link creation, and atomic completion persistence.
- [ ] Run `uv run pytest tests/converter_copilot/test_sqlite_store.py tests/converter_copilot/test_task_api.py -q`; expected: new assertions fail before the repository methods exist.
- [ ] Implement canonical request hashing, transaction-scoped queue event creation, retry count checking and insert, and terminal report/status/event writes in `SQLiteStore`.
- [ ] Add optional `Idempotency-Key` header handling to task creation and retry routes, and allow that header in CORS; return the existing 202 response for a matching key and HTTP 409 for a changed task-create payload.
- [ ] Re-run the targeted command; expected: task API and repository tests pass, including prior retry behavior.
- [ ] Commit as `feat: make task persistence idempotent`.

### Task 3: Add leased worker and restart recovery

**Files:**
- Modify: `src/converter_copilot/api/runner.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/config.py`
- Modify: `src/converter_copilot/api/main.py`
- Test: `tests/converter_copilot/test_task_api.py`
- Test: Create `tests/converter_copilot/test_task_recovery.py`

**Interfaces:**
- `TaskRunner.start() -> None`, `TaskRunner.stop() -> None`, and `TaskRunner.run_once() -> bool` (true if one task was claimed).
- `SQLiteStore.claim_next_task(worker_id: str, *, lease_seconds: int) -> TaskRecord | None` atomically claims one queued or expired task and increments attempt.
- `SQLiteStore.renew_task_lease(task_id: str, worker_id: str, *, lease_seconds: int) -> bool` only renews the current owner's lease.
- `SQLiteStore.requeue_expired_tasks() -> list[str]` appends a recovery event when it requeues each expired task.
- Worker settings: `CONVERTER_WORKER_CONCURRENCY=1`, `CONVERTER_WORKER_POLL_SECONDS=0.5`, `CONVERTER_TASK_LEASE_SECONDS=60`, `CONVERTER_TASK_TIMEOUT_SECONDS=1800`, `CONVERTER_TASK_MAX_ATTEMPTS=3`; validate positive values at startup.
- `TaskRecord` adds `attempt: int`, `max_attempts: int`, `lease_expires_at: datetime | None`, and `error_code: str | None`; internal `lease_owner` is never serialized.

- [ ] Add deterministic fake-graph tests `test_queued_task_runs_after_runner_restart`, `test_expired_running_lease_is_requeued_once`, `test_expired_task_at_attempt_limit_becomes_failed`, `test_stale_worker_cannot_overwrite_new_lease_result`, `test_worker_never_exceeds_configured_concurrency`, and `test_failed_attempt_persists_safe_error_category`; add an API readiness test for migration failure.
- [ ] Run `uv run pytest tests/converter_copilot/test_task_recovery.py -q`; expected: tests fail because no persistent claim/lease loop exists.
- [ ] Implement transactional claim, lease renewal, bounded worker loop, per-task timeout, safe failure category, and startup scan for expired leases. Requeue expired attempts until `CONVERTER_TASK_MAX_ATTEMPTS`; then persist `failed`. Fence report/failure writes by worker ID so a task whose lease was taken over cannot overwrite the new owner's result. A newly started worker must claim previously queued rows without relying on in-memory `_active` state.
- [ ] Add FastAPI lifespan startup/shutdown to initialize the store and start/await the worker; capture initialization failure in app state, skip worker startup, keep `/api/health` as liveness, and make `/api/ready` report a safe 503 reason.
- [ ] Re-run recovery and existing task API tests; expected: recovery tests pass and existing API tests still receive terminal reports.
- [ ] Commit as `feat: recover durable tasks after restart`.

### Task 4: Expose attempts and recovery state in the Web UI

**Files:**
- Modify: `src/converter_copilot/domain/schemas.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/TaskPage.tsx`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/src/components/TaskProgress.tsx`
- Test: `tests/converter_copilot/test_task_api.py`
- Test: `web/src/pages/TaskPage.test.tsx`

**Interfaces:**
- `TaskRecord` API adds `attempt`, `max_attempts`, `lease_expires_at`, and nullable `error_code`; no worker ID or secret-bearing diagnostic is returned.
- Recovery events use stage `recovered`, status `queued`, and a localized safe message.

- [ ] Add API/UI tests for a recovered task rendering its attempt number, recovery event, and safe error guidance.
- [ ] Run `uv run pytest tests/converter_copilot/test_task_api.py -q` and `npm --prefix web test -- --run src/pages/TaskPage.test.tsx`; expected: new UI fields are initially absent.
- [ ] Extend task response types, send a per-action `Idempotency-Key` on create/retry requests, and render queue/running/recovered/terminal state, attempt count, and safe error guidance without changing the existing SSE cursor contract.
- [ ] Re-run the targeted tests and `npm --prefix web run build`; expected: API/UI recovery behavior passes and TypeScript production build succeeds.
- [ ] Commit as `feat: show durable task recovery in workspace`.

### Task 5: Document local recovery operations

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `.env.example`

- [ ] Document SQLite backup-before-migration, single-instance data-directory rule, worker settings, lease-based restart behavior, at-least-once model-call caveat, readiness meaning, and safe error display.
- [ ] Review the README commands against the actual local and Docker Compose entry points; expected: each documented command maps to an existing script or service.
- [ ] Commit as `docs: explain local task recovery`.
