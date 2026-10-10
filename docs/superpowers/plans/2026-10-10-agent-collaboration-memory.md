# Agent Collaboration and Auditable Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Native execution was selected by the user; implement tasks in this session without delegating.

**Goal:** Add bounded role-based collaboration, an auditable fault-hypothesis tree, human-approved memory relations, and a web/evaluation workflow to the local converter engineering copilot.

**Architecture:** Extend the existing LangGraph fault-analysis path with explicit coordinator, evidence researcher, hypothesis analyst, branch auditor, deterministic evidence gate, and report synthesis stages. Persist role runs and branch records in SQLite, project eligible accepted reviews into provenance-linked memory relations, expose read APIs, and render the execution and memory provenance in the existing React work-order flow.

**Tech Stack:** Python 3.11+, LangGraph, Pydantic, FastAPI, SQLite schema migrations, React, TypeScript, Vitest, pytest, existing DeepSeek-compatible model adapter and Chroma retrieval.

**Spec:** `docs/superpowers/specs/2026-10-10-agent-collaboration-memory-design.md`

## Global Constraints

- Local single-user operation; no public deployment, authentication, or multi-tenant behavior.
- Fault-analysis collaboration is bounded: at most 3 root hypotheses, one audit pass per hypothesis, bounded retries, and a configured total task timeout.
- Do not persist or expose hidden raw CoT, full prompts/conversation history, API keys, or unredacted exception text.
- Reuse existing evidence IDs, `validate_report_evidence`, task events/SSE, work-order review history, SQLite backup/migration behavior, and React evidence drawer.
- Only an `accepted` human work-order review with valid non-synthetic evidence provenance can create reusable memory relations.
- Similarity is a retrieval cue, not proof of causality; engineering advice remains advisory and read-only.
- Synthetic records remain labeled and are excluded from claims about real field accuracy.
- Do not add a graph database or new external service; SQLite remains the source of truth for this local V1.3 feature.

## Review Focus

- **Insufficient operating conditions:** retain the existing clarification/partial path and do not launch fault hypotheses (`test_fault_workflow_does_not_expand_without_required_context`).
- **Empty, conflicting, cross-equipment, or unavailable evidence:** branch audit must preserve gaps/conflicts and deterministic gates must not promote unsupported candidates (`test_hypothesis_gate_blocks_unsupported_and_conflicting_branches`).
- **Partial agent failure or budget exhaustion:** mark affected branch incomplete and cap calls/retries rather than presenting it as audited (`test_auditor_failure_returns_partial_branch_and_respects_budget`).
- **Rejected, incomplete, synthetic, or revoked reviews:** no active reusable memory edge may be returned (`test_memory_projection_requires_accepted_review_and_valid_provenance`).
- **Repeated projection and service restart:** stable IDs and provenance revisions must avoid duplicate relations and preserve task traces (`test_memory_projection_is_idempotent_and_restart_readable`).

---

### Task 1: Agent and memory domain contracts plus SQLite persistence

**Files:**
- Create: `src/converter_copilot/domain/agent_audit.py`
- Modify: `src/converter_copilot/storage/migrations.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Test: `tests/converter_copilot/test_agent_audit_store.py`
- Test: `tests/converter_copilot/test_memory_store.py`

**Interfaces:**
- `AgentRole`: `coordinator | evidence_researcher | hypothesis_analyst | evidence_auditor | report_synthesizer`.
- `AgentRunRecord`: `agent_run_id`, `task_id`, `role`, `stage`, `status`, `parent_run_id`, optional `branch_id`, `input_refs`, `output_refs`, `summary`, optional safe `error_code`, `retry_count`, `started_at`, optional `completed_at`, optional `provider_usage`.
- `HypothesisBranch`: `branch_id`, `task_id`, optional `parent_branch_id`, `hypothesis`, `mechanism_summary`, `supporting_evidence_ids`, `opposing_evidence_ids`, `applicability`, `missing_fields`, `checks`, `gate_status`, `disposition`, `ordinal`.
- `MemoryNode`: stable `node_id`, controlled `node_type`, `canonical_key`, `display_name`, `synthetic_demo`.
- `MemoryEdge`: stable `edge_id`, `source_node_id`, controlled `relation_type`, `target_node_id`, source task/work-order/review version, `evidence_ids`, `synthetic_demo`, `active`, timestamps.
- Increment SQLite schema to version 8. Add `agent_runs`, `hypothesis_branches`, `memory_nodes`, and `memory_edges` with task/source foreign keys, controlled status/type checks, unique stable IDs, and indexes by task, branch, canonical key, and source review.
- `SQLiteStore.save_agent_run(record)`, `list_agent_runs(task_id)`, `replace_hypothesis_branches(task_id, branches)`, `list_hypothesis_branches(task_id)`, `upsert_memory_graph(nodes, edges)`, and `list_memory_edges(...)` return typed records and use parameterized SQL.

- [ ] **Step 1: Write failing store contract tests** for record round-trip, stable ordering, invalid task FK, migration from schema 7, and duplicate branch/edge IDs.
- [ ] **Step 2: Run the targeted tests and confirm the new contract is missing.**

Run: `python -m pytest tests/converter_copilot/test_agent_audit_store.py tests/converter_copilot/test_memory_store.py -q`

Expected: FAIL on the new APIs/schema before implementation.

- [ ] **Step 3: Define Pydantic records in `domain/agent_audit.py`** with enum/literal fields, bounded summary/note lengths, timestamp validation, and no raw prompt fields.
- [ ] **Step 4: Add migration 8** with foreign keys and indexes; preserve the existing ordered transaction/backup migration path.
- [ ] **Step 5: Implement typed SQLiteStore save/list methods** with stable ordering and idempotent upserts only where provenance identity matches.
- [ ] **Step 6: Run the targeted tests and migration tests.**

Run: `python -m pytest tests/converter_copilot/test_agent_audit_store.py tests/converter_copilot/test_memory_store.py -q`

Expected: PASS; previous schema 7 data remains readable and new records are queryable after reinitialization.

- [ ] **Step 7: Commit** as `feat: persist agent traces and hypothesis branches`.

### Task 2: Bounded role-based LangGraph fault workflow

**Files:**
- Create: `src/converter_copilot/agent/audit_prompts.py`
- Create: `src/converter_copilot/agent/collaboration.py`
- Modify: `src/converter_copilot/agent/state.py`
- Modify: `src/converter_copilot/agent/workflows.py`
- Modify: `src/converter_copilot/agent/prompts.py`
- Modify: `src/converter_copilot/config.py`
- Test: `tests/converter_copilot/test_agent_collaboration_workflow.py`

**Interfaces:**
- Extend `WorkflowDeps` with optional `agent_event_sink: Callable[[AgentRunRecord], None]`, optional `agent_result_sink: Callable[[list[HypothesisBranch]], None]`, and `AgentBudget(max_hypotheses=3, max_parallel_auditors=3, max_audit_passes=1, max_model_calls=10)`; validate configured values against hard maxima.
- Extend `build_workflow(mode, deps, *, agent_strategy="collaborative")`; `agent_strategy="baseline"` compiles the current one-shot fault-analysis path for offline comparison, and is rejected for modes other than fault analysis only when the caller explicitly requests an unsupported strategy.
- Add `make_hypothesis_node(deps, budget)`, `make_branch_audit_node(deps, budget)`, `make_evidence_gate_node()`, and `make_synthesis_node(deps)`.
- Extend `ConverterState` with `agent_runs`, `hypothesis_branches`, `agent_budget_remaining`, and `agent_warnings`; keep `inputs`, `evidence`, and `report` compatible.
- Keep technical-research and case-retrieval graph branches unchanged in this version.

- [x] **Step 1: Write failing workflow tests** for role ordering, maximum three branches, bounded audit calls, evidence ID integrity, partial branch on auditor error, and legacy report compatibility.
- [x] **Step 2: Run the focused tests to confirm they fail for missing collaboration behavior.**

Run: `python -m pytest tests/converter_copilot/test_agent_collaboration_workflow.py -q`

Expected: FAIL before the new graph stages are implemented.

- [x] **Step 3: Add role-specific structured output contracts** for candidate hypotheses and branch audits; prompts request concise rationale summaries and exact evidence IDs only.
- [x] **Step 4: Implement coordinator planning and budget accounting** after intake; if required context is missing, route to clarification without spending hypothesis/audit calls.
- [x] **Step 5: Implement bounded hypothesis generation and parallel per-branch audit** using injected model/search dependencies; cap roots at 3, audit each branch once, and preserve failed branches as incomplete.
- [x] **Step 6: Implement deterministic evidence gate** using existing evidence status/applicability and citation validation; model confidence cannot override absent support, explicit conflict, or incompatible equipment applicability.
- [x] **Step 7: Synthesize the existing `FaultAnalysisReport` and `ConverterReport`** from passing/qualified branches, preserving claims/evidence IDs and adding safe quality warnings for omitted/incomplete branches.
- [x] **Step 8: Run the focused workflow and evidence-quality tests.**

Run: `python -m pytest tests/converter_copilot/test_agent_collaboration_workflow.py tests/converter_copilot/test_evidence_quality.py -q`

Expected: PASS with deterministic fake model/search adapters and no network calls.

- [ ] **Step 9: Commit** as `feat: add bounded fault-analysis agent collaboration`.

### Task 3: Durable agent-run events and runner integration

**Files:**
- Modify: `src/converter_copilot/api/runner.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Test: `tests/converter_copilot/test_agent_run_lifecycle.py`

**Interfaces:**
- `TaskRunner._run_task` creates a task-scoped `WorkflowDeps` copy with sinks that persist `AgentRunRecord` and `HypothesisBranch` using Task 1 methods.
- Agent lifecycle writes append the existing `task_events` format with stable stages `agent:{role}` and safe status summaries; SSE remains the single event stream.
- Terminal agent run records include safe error categories only; no exception strings, prompts, provider secrets, or hidden CoT.

- [ ] **Step 1: Write failing runner tests** for start/completion persistence, sanitized failure, task reload, and monotonically ordered task event IDs.
- [ ] **Step 2: Run the focused tests and confirm no agent lifecycle records are emitted.**

Run: `python -m pytest tests/converter_copilot/test_agent_run_lifecycle.py -q`

Expected: FAIL before runner sinks are wired.

- [ ] **Step 3: Bind persistence sinks per task** without mutating shared dependency providers; persist start and terminal state with stable run IDs.
- [ ] **Step 4: Keep branch updates and report commit fenced by the current task lease** so stale attempts cannot overwrite the latest run.
- [ ] **Step 5: Verify restart reads and SSE replay** from `events_after` after creating agent lifecycle records.
- [ ] **Step 6: Run runner recovery, task event, and new agent lifecycle tests.**

Run: `python -m pytest tests/converter_copilot/test_agent_run_lifecycle.py tests/converter_copilot/test_durable_task_recovery.py -q`

Expected: PASS; old task events remain readable and new agent events replay after reconnect.

- [ ] **Step 7: Commit** as `feat: persist agent lifecycle in task events`.

### Task 4: Read APIs for agent traces and hypothesis branches

**Files:**
- Modify: `src/converter_copilot/api/routes/tasks.py`
- Modify: `src/converter_copilot/api/models.py`
- Modify: `src/converter_copilot/domain/agent_audit.py`
- Test: `tests/converter_copilot/test_agent_audit_api.py`

**Interfaces:**
- `GET /api/tasks/{task_id}/agents` returns `{task_id, items: AgentRunRecord[]}` in stable start/run order.
- `GET /api/tasks/{task_id}/hypotheses` returns `{task_id, items: HypothesisBranch[]}` ordered by ordinal, then branch ID.
- Unknown task IDs return 404; responses omit internal prompt text, credentials, raw exceptions, and provider trace payloads.

- [ ] **Step 1: Write failing API tests** for existing/unknown tasks, ordering, response schema, and forbidden sensitive fields.
- [ ] **Step 2: Run focused API tests and confirm endpoints are absent.**

Run: `python -m pytest tests/converter_copilot/test_agent_audit_api.py -q`

Expected: FAIL because routes are not registered.

- [ ] **Step 3: Add typed response models and task-scoped GET routes** using Task 1 store methods.
- [ ] **Step 4: Run API tests plus existing task route tests.**

Run: `python -m pytest tests/converter_copilot/test_agent_audit_api.py tests/converter_copilot/test_task_api.py -q`

Expected: PASS; legacy task/report/events API shapes are unchanged.

- [ ] **Step 5: Commit** as `feat: expose agent trace and hypothesis APIs`.

### Task 5: Human-approved memory projection and query API

**Files:**
- Create: `src/converter_copilot/domain/memory.py`
- Create: `src/converter_copilot/api/routes/memory.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/api/routes/work_orders.py`
- Modify: `src/converter_copilot/api/main.py`
- Test: `tests/converter_copilot/test_reviewed_memory.py`
- Test: `tests/converter_copilot/test_memory_api.py`

**Interfaces:**
- `project_accepted_review(work_order_id: str, task_id: str, review_version: int) -> int` projects only typed, source-linked, non-synthetic accepted records and returns inserted/updated edge count.
- `search_memory(query: str | None, equipment_type: str | None, limit: int = 10) -> list[MemoryMatch]` returns only active edges whose latest source review remains accepted, plus provenance and applicability differences.
- `GET /api/memory?q=&equipment_type=&limit=10` returns `{items: MemoryMatch[], synthetic_demo: false}`; `limit` is bounded to 1–50.
- Accepted review persistence and memory projection use one SQLite transaction or a transactionally recorded projection state; corrections/rejections invalidate earlier active edges by source review version.

- [ ] **Step 1: Write failing tests** for acceptance eligibility, rejected/incomplete/synthetic/no-evidence exclusion, revoke/correction invalidation, idempotent replay, and query provenance.
- [ ] **Step 2: Run focused tests and confirm no reviewed memory projection exists.**

Run: `python -m pytest tests/converter_copilot/test_reviewed_memory.py tests/converter_copilot/test_memory_api.py -q`

Expected: FAIL before the projection service and route are added.

- [ ] **Step 3: Implement controlled node/edge projection** from accepted work-order analysis and source evidence; derive canonical keys from normalized structured values, never free-form unreviewed model text.
- [ ] **Step 4: Make review save plus projection/invalidation transaction-safe and idempotent** for each `(work_order_id, task_id, version)` source identity.
- [ ] **Step 5: Add bounded memory query service and read-only API** with source case, review version, evidence IDs, synthetic label, and differences.
- [ ] **Step 6: Run memory unit, transaction, API, and existing work-order review tests.**

Run: `python -m pytest tests/converter_copilot/test_reviewed_memory.py tests/converter_copilot/test_memory_api.py tests/converter_copilot/test_work_order_review.py -q`

Expected: PASS; no rejected, incomplete, synthetic, or source-less item is returned as reusable memory.

- [ ] **Step 7: Commit** as `feat: add human-reviewed engineering memory`.

### Task 6: Agent timeline and hypothesis tree in the Web task/report views

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api/client.ts`
- Create: `web/src/components/AgentTimeline.tsx`
- Create: `web/src/components/HypothesisTree.tsx`
- Modify: `web/src/pages/TaskPage.tsx`
- Modify: `web/src/pages/ReportPage.tsx`
- Modify: `web/src/styles.css`
- Test: `web/src/components/AgentTimeline.test.tsx`
- Test: `web/src/components/HypothesisTree.test.tsx`

**Interfaces:**
- `getAgentRuns(taskId): Promise<AgentRunRecord[]>` and `getHypothesisBranches(taskId): Promise<HypothesisBranch[]>` use the Task 4 routes.
- `AgentTimeline` renders role, status, duration, safe summary, and retry; it clearly labels generated summaries as execution notes, not raw CoT.
- `HypothesisTree` renders at most the returned bounded branches with support/opposition counts, applicability, gate disposition, missing fields, checks, and `EvidenceDrawer` navigation by evidence ID.

- [ ] **Step 1: Write failing component tests** for in-progress/failed/incomplete agents, empty states, supported/opposing evidence links, unknown evidence IDs, and accessible status labels.
- [ ] **Step 2: Run targeted Vitest tests and confirm components are absent.**

Run: `npm test -- --run src/components/AgentTimeline.test.tsx src/components/HypothesisTree.test.tsx`

Expected: FAIL before the new components are implemented.

- [ ] **Step 3: Add typed client methods and fallback behavior** for absent records on old tasks without hiding API failures.
- [ ] **Step 4: Implement accessible timeline and hypothesis tree** using existing visual tokens, buttons, and evidence drawer contracts.
- [ ] **Step 5: Integrate the timeline on `TaskPage` and the branch comparison on `ReportPage`**; retain all current report sections and markdown export.
- [ ] **Step 6: Run targeted component tests and production build.**

Run: `npm test -- --run src/components/AgentTimeline.test.tsx src/components/HypothesisTree.test.tsx src/pages/TaskPage.test.tsx src/pages/ReportPage.test.tsx` and `npm run build`

Expected: PASS; TypeScript production bundle builds with the legacy task/report UI intact.

- [ ] **Step 7: Commit** as `feat: visualize agent collaboration and hypotheses`.

### Task 7: Related reviewed memory in work-order UI

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/WorkOrdersPage.tsx`
- Modify: `web/src/styles.css`
- Test: `web/src/pages/WorkOrdersMemory.test.tsx`

**Interfaces:**
- `searchMemory(query, equipmentType, limit): Promise<MemoryMatch[]>` calls `GET /api/memory`.
- Work-order view queries only after a work order is selected and shows source case/review, equipment and condition differences, and linked evidence; similarity is labeled as retrieval relevance, never as cause probability.

- [ ] **Step 1: Write failing UI tests** for matching memory, no-match state, synthetic/unreviewed exclusion, and provenance/evidence navigation.
- [ ] **Step 2: Run the focused Vitest test to confirm the panel is absent.**

Run: `npm test -- --run src/pages/WorkOrdersMemory.test.tsx`

Expected: FAIL before the memory section is implemented.

- [ ] **Step 3: Implement typed API access and a compact reviewed-memory section** that preserves existing work-order review actions.
- [ ] **Step 4: Run memory/work-order UI tests and the production build.**

Run: `npm test -- --run src/pages/WorkOrdersMemory.test.tsx src/pages/WorkOrdersPage.test.tsx` and `npm run build`

Expected: PASS; unreviewed data is never shown as reusable memory.

- [ ] **Step 5: Commit** as `feat: show reviewed memory on work orders`.

### Task 8: Offline comparison, operations documentation, and full integration

**Files:**
- Modify: `src/converter_copilot/evaluation/runner.py`
- Modify: `src/converter_copilot/evaluation/metrics.py`
- Modify: `web/src/pages/EvaluationPage.tsx`
- Modify: `web/src/types.ts`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `README.md`
- Test: `tests/converter_copilot/test_collaboration_evaluation.py`
- Test: `web/src/pages/EvaluationCollaboration.test.tsx`

**Interfaces:**
- Evaluation summaries add `workflow_version`, `agent_strategy`, `branch_metrics`, `agent_latency_ms`, `agent_run_counts`, `budget_exhaustion_count`, and metric sample counts; fields remain null/unavailable when source labels or provider usage are absent.
- Extend `evaluate_records(*, mode, records, graph, strategy_graphs: Mapping[str, Any] | None = None, recall_at_k=5, dataset_version="unspecified")`; when `strategy_graphs` is supplied, execute each strategy against the same record sequence and return per-strategy summaries plus a paired comparison, while preserving the existing single-graph response shape when omitted.
- Add `compute_branch_metrics(branches, reviewed_labels) -> dict[str, Any]`; report only deterministic coverage/integrity/count metrics and null labeled-quality metrics when human labels are insufficient.
- `branch_metrics` report evidence-ID integrity, support/opposition coverage, unsupported candidate share, applicability warning share, and incomplete-review share without naming them as truth/accuracy metrics absent human gold labels.
- UI displays dataset source, label counts, workflow version, agent strategy, branch metrics, latency, and unavailable-usage reason.

- [ ] **Step 1: Write failing evaluation tests** comparing baseline and collaborative modes over the same records, separating synthetic/manual samples, and returning null for unavailable labels/usage.
- [ ] **Step 2: Run focused evaluation tests and confirm comparison fields are missing.**

Run: `python -m pytest tests/converter_copilot/test_collaboration_evaluation.py -q`

Expected: FAIL before evaluation records expose collaboration metrics.

- [ ] **Step 3: Extend `evaluate_records`** to run named baseline/collaboration strategies with deterministic adapters, retaining per-run task/branch outcomes and no network calls in unit tests.
- [ ] **Step 4: Add branch-level deterministic metrics and truthful sample counts**; never infer semantic truth from citation presence or model self-confidence.
- [ ] **Step 5: Extend the evaluation view** and mark synthetic-only results as workflow checks, not field-accuracy evidence.
- [ ] **Step 6: Document architecture, role contracts, budget defaults, memory eligibility/revocation, restart/migration behavior, local API routes, and safety boundary.**
- [ ] **Step 7: Run backend collaboration/evaluation tests, relevant existing work-order/evidence tests, frontend tests, lint, and build.**

Run: `python -m pytest tests/converter_copilot -q`; `npm test -- --run`; `npm run build`; `ruff check src/converter_copilot`

Expected: PASS; deterministic tests make no DeepSeek/Tavily requests, and any environment-dependent integration limitation is reported separately.

- [ ] **Step 8: Commit** as `feat: evaluate and document agent collaboration`.

## Dependency and Delivery Order

| Deliverable | Depends on |
|---|---|
| Domain records, migration, trace storage | none |
| Bounded collaboration graph | Task 1 |
| Durable runner events | Tasks 1–2 |
| Trace and hypothesis APIs | Tasks 1–3 |
| Approved memory projection/query API | Task 1 and current review history |
| Agent timeline and hypothesis visualization | Task 4 |
| Work-order memory panel | Task 5 |
| Offline comparisons and user documentation | Tasks 2–7 |

The implementation is complete only when all eight deliverables work together in the existing local work-order flow and original task/report behavior remains usable. No public deployment or device-write capability is part of this plan.
