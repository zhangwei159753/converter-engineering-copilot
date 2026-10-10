# Idempotent Document Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate, import, deduplicate, inspect, repair, and delete local technical documents without leaving silent SQLite/Chroma partial writes.

**Architecture:** A format-specific validator completes before persistent writes. Source bytes are retained under the local data directory, and content plus parser/chunker versions determine stable document, chunk, and case IDs. SQLite holds an import ledger; Chroma uses idempotent upserts. Recovery replays incomplete imports and reports irreparable records for re-upload.

**Tech Stack:** Python, FastAPI UploadFile, PyMuPDF, SQLite, Chroma, FastEmbed, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-local-reliability-v1-design.md`

## Global Constraints

- Depends on the versioned migration interface from `2026-10-10-durable-task-recovery.md`.
- Validate the complete file before creating an import row, case, vector, or retained source file.
- Same bytes plus the same parser/chunker version must yield the same import, document, chunk, and case IDs.
- SQLite and Chroma do not share an atomic transaction; expose incomplete state and repair it through idempotent replay.
- Keep the app local and single-instance; do not add device control or outbound enterprise integrations.
- The UI must identify imported demo records as synthetic where the source dataset says so.

## Review Focus

- Truncated/invalid UTF-8 CSV: test that no case, ledger row, vector, or source file is created.
- CSV duplicate/missing headers, missing symptom, malformed quoting, and oversized rows: test deterministic row/column errors.
- Identical upload and parser-version change: test stable identity for the former and explicit new version for the latter.
- Chroma succeeds but SQLite case write fails, and the reverse: test replay reaches one complete record set.
- Missing retained source file during repair or deletion failure: test `needs_repair` visibility and re-upload guidance.

---

### Task 1: Validate complete uploads and enforce documented limits

**Files:**
- Create: `src/converter_copilot/retrieval/import_validation.py`
- Modify: `src/converter_copilot/api/routes/cases.py`
- Modify: `src/converter_copilot/config.py`
- Modify: `src/converter_copilot/retrieval/document_loader.py`
- Test: `tests/converter_copilot/test_document_loader.py`
- Test: `tests/converter_copilot/test_case_api.py`

**Interfaces:**
- `ImportLimits.from_env() -> ImportLimits` validates positive byte/page/row/chunk ceilings.
- `validate_upload(filename: str, payload: bytes, limits: ImportLimits) -> ValidatedImport` parses all rows/pages before persistence and returns file type, parsed rows, content SHA-256, and parser/chunker version.
- `ImportLimits` contains `max_bytes`, `max_pdf_pages`, `max_csv_rows`, and `max_chunks`; `ValidatedImport` contains normalized filename, file type, SHA-256, parser/chunker versions, parsed `DocumentChunk` list, parsed CSV case rows, and original bytes.
- `import_id = sha256("converter-import-v1:" + file_type + ":" + parser_version + ":" + chunker_version + ":" + content_sha256)[:32]`; `document_id` equals `import_id`; chunk IDs hash import ID, stable locator, and normalized chunk text; CSV case IDs hash import ID plus row number.
- Initial defaults: `IMPORT_MAX_BYTES=20971520` (20 MiB), `IMPORT_MAX_PDF_PAGES=200`, `IMPORT_MAX_CSV_ROWS=10000`, `IMPORT_MAX_CHUNKS=2000`.
- Stable API errors use `detail: {"code": str, "message": str, "filename": str, "row": int | null}`.

- [x] Add tests `test_invalid_csv_has_no_import_side_effects`, `test_missing_symptom_reports_csv_row`, `test_duplicate_headers_are_rejected`, `test_file_byte_limit_is_enforced_before_reading_unbounded_body`, `test_pdf_page_limit_is_enforced`, and `test_chunk_limit_is_enforced`.
- [x] Run `uv run pytest tests/converter_copilot/test_document_loader.py tests/converter_copilot/test_case_api.py -q`; expected: validation and no-side-effect assertions fail on current import-first implementation.
- [x] Stream the request in bounded chunks up to the byte ceiling; validate strict CSV UTF-8, unique headers, required `symptom`, row shape, PDF readability/page count, Markdown decode/non-empty content, and resulting chunk count before obtaining vector dependencies.
- [x] Return stable error codes and safe row/field details; do not include uploaded text in error bodies.
- [x] Re-run targeted tests; expected: every malformed or over-limit input leaves no persistent import/case/vector state.
- [x] Commit as `feat: validate source imports before persistence`.

### Task 2: Persist import ledger, source bytes, and stable identities

**Files:**
- Modify: `src/converter_copilot/storage/migrations.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/retrieval/document_loader.py`
- Modify: `src/converter_copilot/config.py`
- Modify: `.env.example`
- Test: `tests/converter_copilot/test_sqlite_store.py`
- Test: `tests/converter_copilot/test_document_loader.py`

**Interfaces:**
- `SQLiteStore.create_import(import_id: str, *, content_sha256: str, source_name: str, file_type: str, parser_version: str, chunker_version: str, expected_chunk_count: int, source_path: str) -> ImportRecord`.
- `SQLiteStore.get_import(import_id: str) -> ImportRecord | None` and `SQLiteStore.update_import_status(import_id: str, status: ImportStatus, *, indexed_chunks: int, imported_cases: int, error_code: str | None = None) -> ImportRecord`.
- `SQLiteStore.save_import_case_candidates(import_id: str, candidates: list[CaseRecord]) -> None` stores extraction output and deterministic case IDs before vector writes, making replay independent of another LLM extraction.
- `ImportStatus` is one of `validated`, `indexing`, `complete`, `failed`, `needs_repair`, `deleting`, or `deleted`. `ImportRecord` contains import/document IDs, content hash, source filename/type/path, parser/chunker versions, expected/indexed chunk counts, imported case count, status, timestamps, and nullable safe error code.
- `load_document(path: Path, task_id: str, *, document_id: str, parser_version: str, chunker_version: str) -> list[DocumentChunk]` derives chunk IDs from document identity plus stable locator/content.
- Source bytes are atomically retained as `.data/sources/{import_id}{suffix}`; `.data` remains git-ignored.

- [x] Add tests for migration/backfill of current cases, unique import digest/version, stable chunk and CSV case IDs, and source-file path survival across temporary directories.
- [x] Run `uv run pytest tests/converter_copilot/test_sqlite_store.py tests/converter_copilot/test_document_loader.py -q`; expected: identity and ledger tests fail before the new schema exists.
- [x] Add an `imports` table and source relationship columns/tables without rewriting existing case payloads; store status, expected/actual counts, digest, versions, timestamps, and safe error code.
- [x] Save validated bytes atomically to the configured local source directory; derive IDs from SHA-256 plus normalized file type and parser/chunker versions, and derive CSV case IDs from import ID plus row locator.
- [x] Re-run targeted tests; expected: same file/version reproduces all IDs and schema migration preserves existing cases.
- [x] Commit as `feat: track idempotent document imports`.

### Task 3: Implement replay, consistency inspection, and deletion compensation

**Files:**
- Modify: `src/converter_copilot/api/routes/cases.py`
- Modify: `src/converter_copilot/api/runner.py`
- Modify: `src/converter_copilot/storage/sqlite_store.py`
- Modify: `src/converter_copilot/retrieval/vector_index.py`
- Test: `tests/converter_copilot/test_case_api.py`
- Test: Create `tests/converter_copilot/test_import_recovery.py`

**Interfaces:**
- `VectorIndex.delete_chunks(chunk_ids: list[str]) -> None` removes exact imported chunk IDs; `delete_document(document_id)` remains available for repair cleanup.
- `SQLiteStore.list_incomplete_imports() -> list[ImportRecord]` and `SQLiteStore.list_import_records() -> list[ImportRecord]`.
- `TaskRunner.reconcile_imports() -> list[ImportRepairResult]` replays `validated`/`indexing` imports idempotently and returns per-import `complete` or `needs_repair` status.
- `ImportRepairResult` contains `import_id`, final status, expected/indexed chunk counts, imported case count, and nullable safe error code.
- API: `GET /api/imports`, `GET /api/imports/{import_id}`, `POST /api/imports/{import_id}/repair`, `DELETE /api/imports/{import_id}`.

- [x] Add injected-failure tests for vector success/database failure, database success/vector failure, crash replay, source file missing, vector mismatch, and delete retry.
- [x] Run `uv run pytest tests/converter_copilot/test_import_recovery.py tests/converter_copilot/test_case_api.py -q`; expected: replay and compensation assertions fail before ledger-driven reconciliation exists.
- [x] Implement ordered status transitions `validated -> indexing -> complete`; for PDF/Markdown, persist extracted case candidates and deterministic case IDs before vector writes; then use vector upsert and case upsert by stable IDs. On startup reconcile incomplete imports after store and vector adapter initialization without rerunning a saved extraction.
- [x] Add consistency inspection for missing expected vectors, orphan cases, and incomplete imports; mark missing-source records `needs_repair` with safe re-upload instructions.
- [x] Make deletion persist a deleting state, remove linked cases and exact vector IDs, then remove retained source bytes and mark deleted; retry incomplete deletion safely.
- [x] Re-run targeted tests; expected: injected partial writes converge without duplicate cases/vectors and failed deletions remain visible.
- [x] Commit as `feat: reconcile partial document imports`.

### Task 4: Show import state and repair controls in the Web workbench

**Files:**
- Modify: `src/converter_copilot/api/models.py`
- Modify: `src/converter_copilot/api/routes/cases.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/pages/WorkspacePage.tsx`
- Modify: `web/src/styles.css`
- Test: `tests/converter_copilot/test_case_api.py`
- Test: `web/src/pages/WorkspacePage.test.tsx`

**Interfaces:**
- Import list/detail responses expose import ID, source filename, state, counts, created/updated times, and safe error code; never expose local absolute source paths.
- `listImports()`, `repairImport(importId)`, and `deleteImport(importId)` are typed Web API client methods.

- [x] Add API/UI tests for duplicate upload notice, failed validation details, incomplete import warning, repair result, and deletion confirmation.
- [x] Run `uv run pytest tests/converter_copilot/test_case_api.py -q` and `npm --prefix web test -- --run src/pages/WorkspacePage.test.tsx`; expected: current UI has no import ledger state.
- [x] Add a source-import list/status panel with counts, safe errors, repair/delete actions, and explicit synthetic-demo attribution where applicable.
- [x] Re-run targeted tests and `npm --prefix web run build`; expected: import lifecycle is understandable and frontend types/build remain valid.
- [x] Commit as `feat: show source import health in workspace`.

### Task 5: Document import limits and operator repair steps

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/demo-scenario.md`
- Modify: `.env.example`

- [x] Document exact default limits, override variables, local source retention path, digest/version deduplication, retry/repair API, deletion behavior, and when re-upload is required.
- [x] Review the demo walkthrough to confirm synthetic examples remain labeled as synthetic and import completion is not described as engineering validation.
- [x] Commit as `docs: explain import recovery and data limits`.
