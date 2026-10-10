# Work Order Copilot V1.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local, read-only synthetic work-order workflow that links an engineering ticket to an existing analysis task, report, and human review history.

**Architecture:** A package-local fixture adapter serves typed synthetic work orders. FastAPI reuses `TaskRunner` for analysis and SQLite for analysis links plus append-only reviews. A dedicated React workbench presents the ticket queue and review loop.

**Tech Stack:** Python, Pydantic, FastAPI, SQLite migrations, React, TypeScript, existing task workflow.

**Spec:** `docs/superpowers/specs/2026-10-10-work-order-copilot-v1.2-design.md`

## Global Constraints

- Local single-user usage; no public deployment.
- Synthetic work orders are always labeled as demo data.
- Connector is read-only. Never add device writes, parameter dispatch, or safety-procedure replacement.
- Analysis uses the existing durable `fault_analysis` runner and idempotency semantics.
- Human review records are local annotations, not verified identity or external execution.
- No model API call is needed to list tickets, view details, or read review history.

## Review Focus

- Unknown work-order IDs and analysis task IDs return safe 404/409 responses.
- Retried analysis request with same idempotency key does not create duplicate association.
- Non-terminal tasks cannot be reviewed; reviewer/verdict/note validation is enforced.
- Re-review appends a new version and preserves prior values.
- Empty/missing work-order fields are visible and mapped without inventing conditions.

---

### Task 1: Work-order contracts, fixture adapter, and persistence

**Files:**
- Create: `src/converter_copilot/domain/work_orders.py`
- Modify: `src/converter_copilot/storage/migrations.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/domain/schemas.py`

**Interfaces:**
- `WorkOrderRecord` carries `work_order_id`, equipment model/serial, firmware, alarm code, symptom, occurrence time, operating conditions, available attachments, missing fields, severity, status, and `synthetic_demo=True`.
- `list_work_orders(query: str | None, status: str | None) -> list[WorkOrderRecord]` and `get_work_order(work_order_id: str) -> WorkOrderRecord | None` read a small package-local synthetic fixture.
- SQLite tables `work_order_analyses(work_order_id, task_id, created_at)` and `work_order_review_events(event_id, work_order_id, task_id, version, reviewer, verdict, note, reviewed_at)` retain links and append-only review history.
- `SQLiteStore.link_work_order_task(...)`, `list_work_order_tasks(...)`, `save_work_order_review(...)`, and `list_work_order_reviews(...)` provide persistence. Link creation is idempotent; review validation and version allocation happen in one transaction.

- [x] Implement typed fixture adapter and use cases for search/status filtering.
- [x] Add schema migration and transaction-safe persistence methods.
- [x] Add API-ready review request/response schemas with verdict enum and bounded text fields.
- [x] Run Ruff on changed Python modules and inspect migration/API consistency.

### Task 2: Read-only API and task launch mapping

**Files:**
- Create: `src/converter_copilot/api/routes/work_orders.py`
- Modify: `src/converter_copilot/api/main.py`
- Modify: `src/converter_copilot/api/runner.py`

**Interfaces:**
- Router exposes the four paths defined in the spec.
- Analysis mapping uses `equipment_type`, `symptom`, `fault_code`, and only present operating conditions; task association is persisted after runner creation and returned with task.
- Detail response includes task records, reports when available, current review and full review history. Review writes require terminal task status and matching work-order association.

- [x] Add list/detail endpoints with safe filters and explicit synthetic source label.
- [x] Add idempotent analysis endpoint and persistent ticket/task association.
- [x] Add review endpoint with 404/409/422 handling and history output.
- [x] Run Ruff on changed Python modules and inspect API OpenAPI registration.

### Task 3: Work-order Web workbench

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/AppShell.tsx`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/types.ts`
- Create: `web/src/pages/WorkOrdersPage.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Page route `/work-orders`; work-order API functions are strongly typed.
- Queue supports text and status filters. Detail presents field completeness, analysis task lifecycle, report navigation, and review form/history.
- Analysis action navigates to existing task page; report page remains the evidence review surface.

- [x] Add typed client contracts and route/navigation item.
- [x] Build responsive work-order queue/detail interface consistent with current industrial visual language.
- [x] Add analysis and human review interactions, loading/error/empty states, and explicit synthetic/read-only labels.
- [x] Run `npm --prefix web run build` and fix type/build failures.

### Task 4: Documentation and handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/demo-scenario.md`
- Modify: `docs/operations.md`

- [x] Document demo-data boundary, end-to-end workflow, review semantics, and local usage.
- [x] Run final Ruff/build checks and inspect the full diff for scope and secret hygiene.
