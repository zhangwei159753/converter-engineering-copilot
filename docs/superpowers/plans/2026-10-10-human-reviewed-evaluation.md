# Human-Reviewed Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the starter evaluation data into an auditable local review workflow that only scores human-reviewed labels and clearly reports insufficient evidence.

**Architecture:** SQLite stores the evaluation dataset/example metadata and append-only label audit events. Dataset validation remains deterministic and separate from live model execution. A local React evaluation page lets a reviewer inspect source evidence and save labels; metrics only consume reviewed labels and produce explicit `insufficient_labels` reasons.

**Tech Stack:** Python, Pydantic, SQLite, FastAPI, JSONL, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-local-reliability-v1-design.md`

## Global Constraints

- Depends on versioned SQLite migrations from `2026-10-10-durable-task-recovery.md`.
- Preserve current examples as starter/synthetic and unreviewed unless a human explicitly reviews them.
- Empty retrieval gold labels and unreviewed evidence labels yield `null` quality metrics with an explicit reason.
- CI runs deterministic validation and metric fixtures only; no DeepSeek/Tavily/LangSmith calls.
- Reviewer identity is a local free-text attribution in V1, not an authenticated account or authorization claim.
- Never present generated outputs or synthetic scenarios as field-validated engineering outcomes.

## Review Focus

- Gold IDs empty and evidence labels unverified: test null metrics and `insufficient_labels` status.
- Label submitted without reviewer/time or against an unknown example/evidence ID: test rejection.
- Re-review changes a label: test append-only audit history retains the original label.
- Unreviewed labels mixed with reviewed labels: test only reviewed labels contribute to support metrics.
- Starter JSONL contains duplicate IDs, malformed input shape, or invalid status: test validator points to exact file and line.

---

### Task 1: Define dataset and review label contracts

**Files:**
- Modify: `src/converter_copilot/evaluation/metrics.py`
- Modify: `src/converter_copilot/evaluation/runner.py`
- Modify: `evals/converter_copilot/run_eval.py`
- Modify: `evals/converter_copilot/README.md`
- Test: `tests/converter_copilot/test_eval_metrics.py`
- Test: `tests/converter_copilot/test_eval_runner.py`

**Interfaces:**
- Dataset row fields: `example_id`, `source_type`, `label_status`, typed `inputs`, `expected_retrieval_ids`, `manual_evidence_labels`, optional `reviewer_id`, `reviewed_at`, `label_version`.
- `validate_dataset(records: Sequence[Mapping[str, object]], *, mode: str) -> list[DatasetIssue]` checks unique IDs, typed mode inputs, label/status enum and required review metadata.
- `DatasetIssue` contains `example_id: str | None`, `line_number: int | None`, `field: str`, and `message: str`; validation never includes source document text in the message.
- `build_evaluation_summary(...)` adds `metric_status` and `metric_sample_counts`; each unavailable quality metric remains `null` with a reason.
- Each live CLI run exports `review_bundle.json` with `bundle_id`, dataset/mode, and per-example task ID, claims, and evidence snapshots (`evidence_id`, excerpt, source type, locator, URL); it contains no prompts or secrets.
- Bundle names are `{mode}-{UTC timestamp}-review.json` in the selected evaluation output directory; each evidence snapshot carries the matching claim IDs and source-status value.

- [ ] Add tests `test_unreviewed_support_labels_are_not_scored`, `test_empty_gold_labels_report_insufficient_labels`, `test_reviewed_labels_require_reviewer_and_timestamp`, and `test_duplicate_example_id_reports_location`.
- [ ] Run `uv run pytest tests/converter_copilot/test_eval_metrics.py tests/converter_copilot/test_eval_runner.py -q`; expected: explicit status/metadata assertions fail.
- [ ] Add Pydantic or equivalent typed dataset validation and update all three existing JSONL files with explicit `source_type`/`label_status` values while retaining them as unreviewed; extend `evaluate_records` to emit the review bundle snapshots from the returned `ConverterReport`.
- [ ] Make metric aggregation ignore labels unless `label_status == "reviewed"`; return `null` and `insufficient_labels` when the corresponding eligible sample count is zero.
- [ ] Re-run targeted tests; expected: no starter dataset yields a fabricated retrieval or evidence-support score.
- [ ] Commit as `feat: validate evaluation review labels`.

### Task 2: Store reviewed labels and audit changes

**Files:**
- Modify: `src/converter_copilot/storage/migrations.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Create: `src/converter_copilot/evaluation/review.py`
- Test: `tests/converter_copilot/test_sqlite_store.py`
- Test: Create `tests/converter_copilot/test_evaluation_review.py`

**Interfaces:**
- `EvaluationReviewService.list_examples(mode: str | None = None) -> list[EvaluationExample]`.
- `EvaluationReviewService.import_review_bundle(bundle: EvaluationReviewBundle) -> BundleImportResult` is idempotent by `bundle_id` and inserts immutable claim/evidence snapshots into the local review queue.
- `EvaluationReviewService.save_review(example_id: str, *, reviewer_id: str, evidence_labels: list[EvidenceLabel], expected_retrieval_ids: list[str], note: str | None = None) -> EvaluationReview`.
- Review writes create an append-only `evaluation_review_events` row and update the current label projection in one SQLite transaction.
- `EvaluationReviewBundle` contains bundle ID, mode, dataset version, generation time, and per-example task ID plus claim/evidence snapshots. `EvidenceLabel` is `{evidence_id, claim_id, label}` where label is `supported`, `unsupported`, or `unverified`. `EvaluationExample` exposes source type/status, inputs, snapshot claims/evidence, current labels, reviewer/time, and label version. `BundleImportResult` exposes bundle ID and imported/skipped example counts.

- [ ] Add tests for review-bundle idempotency, first review, re-review history, unknown example/evidence IDs, blank reviewer, and transaction rollback on invalid label payload.
- [ ] Run `uv run pytest tests/converter_copilot/test_evaluation_review.py -q`; expected: review API/service contract tests fail before the tables/service exist.
- [ ] Add versioned tables for imported bundle identity, example metadata, immutable claim/evidence snapshots, current review projection, and append-only review events; preserve source type and dataset version.
- [ ] Validate evidence IDs against the example's retrieved/source evidence inventory before accepting reviewed labels.
- [ ] Re-run targeted tests; expected: review history is append-only and invalid reviews persist nothing.
- [ ] Commit as `feat: persist evaluation review history`.

### Task 3: Add local evaluation review API and page

**Files:**
- Create: `src/converter_copilot/api/routes/evaluations.py`
- Modify: `src/converter_copilot/api/main.py`
- Modify: `src/converter_copilot/api/models.py`
- Modify: `web/src/App.tsx`
- Modify: `web/src/types.ts`
- Modify: `web/src/api/client.ts`
- Create: `web/src/pages/EvaluationPage.tsx`
- Modify: `web/src/components/AppShell.tsx`
- Modify: `web/src/styles.css`
- Test: Create `tests/converter_copilot/test_evaluation_api.py`
- Test: Create `web/src/pages/EvaluationPage.test.tsx`

**Interfaces:**
- `GET /api/evaluations` returns dataset versions, per-mode totals, source-type counts, pending/reviewed counts, and metric status/sample counts.
- `POST /api/evaluations/review-bundles` accepts a validated `EvaluationReviewBundle` JSON body and returns imported/skipped counts; repeated `bundle_id` returns the original import summary.
- `GET /api/evaluations/examples?mode=&status=` returns paginated review rows and source evidence locators.
- `PUT /api/evaluations/examples/{example_id}/review` saves one reviewer payload and returns the current review plus audit history.
- `GET /api/evaluations/examples/{example_id}/history` returns append-only reviews.

- [ ] Add API tests for bundle import/deduplication, list/filter/pagination, validation errors, saved review, audit history, and explicit null metrics for unreviewed datasets.
- [ ] Add UI tests for bundle upload, pending/reviewed counts, evidence inspection, review submission, history display, and insufficient-label explanation.
- [ ] Run `uv run pytest tests/converter_copilot/test_evaluation_api.py -q` and `npm --prefix web test -- --run src/pages/EvaluationPage.test.tsx`; expected: endpoints and page are absent.
- [ ] Add the evaluation router and a Web page with review-bundle JSON upload, source type/status filters, evidence excerpt/locator, label controls, reviewer attribution, and metric sample counts.
- [ ] Re-run targeted tests and `npm --prefix web run build`; expected: review actions are visible and only explicitly reviewed labels affect displayed metrics.
- [ ] Commit as `feat: add evaluation review workbench`.

### Task 4: Document review governance and metric limits

**Files:**
- Modify: `evals/converter_copilot/README.md`
- Modify: `README.md`
- Modify: `docs/demo-scenario.md`

- [ ] Document how to review evidence, required reviewer metadata, label versioning, sample counts, metric null reasons, and how to create a versioned baseline only after human review.
- [ ] Verify docs state that a reviewed evidence label is a human annotation and does not establish field correctness or maintenance benefit.
- [ ] Commit as `docs: explain evaluation review workflow`.
