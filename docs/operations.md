# Local operations guide

This guide covers the local single-user application. The API, SQLite database, Chroma collection, retained source files, and review history are intended to run on one host with one API process. Do not point multiple API instances at the same `.data/` directory.

## Start and check the application

Create `.env` from `.env.example` and set provider credentials in that local file. Keep `.env` out of Git and do not put credentials in browser build variables or Docker build arguments.

```powershell
uv sync --frozen
uv run uvicorn converter_copilot.api.main:app --reload --port 8000
```

In a second terminal:

```powershell
cd web
npm ci
npm run dev
```

Open `http://localhost:5173`. `GET /api/health` confirms the API process responds. `GET /api/ready` also confirms schema initialization, SQLite write-lock availability, and worker liveness; it returns HTTP 503 when a required component is unavailable. The OpenAPI page is at `/docs`.

For the local container workflow, create `.env`, then run `docker compose up --build`. This starts local services and is not a public deployment. `docker compose logs -f api` shows API logs; `docker compose down` stops containers while preserving the named data volume.

## Data and upgrades

By default the database, Chroma index, source originals, FastEmbed cache, and evaluation artifacts are under `.data/`. Set `CONVERTER_DB_PATH`, `CHROMA_PERSIST_DIR`, `CONVERTER_SOURCE_DIR`, and `FASTEMBED_CACHE_DIR` to relocate them. Keep these directories on a local disk with enough free space.

SQLite schema changes use ordered migrations. Before upgrading a non-empty database, startup creates an online SQLite backup under `.data/backups/`. If startup fails during backup or migration, fix the reported disk or file access issue before restarting. To restore, stop the API, copy the selected backup over the configured database path, and start the API again. Back up the Chroma directory and retained source directory alongside SQLite when a complete local restore point is needed; SQLite alone does not contain the vector index or source files.

The worker provides at-least-once task execution. A task interrupted during a provider call can make that provider call again after its lease expires. Check the task detail page and local API logs before retrying a terminal task. Worker controls are `CONVERTER_WORKER_CONCURRENCY`, `CONVERTER_WORKER_POLL_SECONDS`, `CONVERTER_TASK_LEASE_SECONDS`, `CONVERTER_TASK_TIMEOUT_SECONDS`, and `CONVERTER_TASK_MAX_ATTEMPTS`. V1 supports one worker process for one local data directory.

## Import repair

Uploads are validated before the import ledger, local source directory, case table, or vector index is changed. Default limits are 20 MiB per file, 200 PDF pages, 10,000 CSV rows, and 2,000 text chunks. CSV input must be UTF-8, use unique non-empty headers, and provide a non-empty `symptom` on every row. Limits can be adjusted with the `IMPORT_MAX_*` variables in `.env`.

The Web workspace's source ledger shows indexed counts and safe error codes. It provides repair for interrupted writes and removal for imported data. If an original source is missing or fails its hash check, upload the same original file again; then retry repair. A partially completed deletion can also be resumed from the ledger. Stable content and parser/chunker identities prevent duplicate chunks and case records during replay.

## Evaluation review

Run a live evaluation with the configured provider key:

```powershell
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis
```

To compare the previous single-workflow baseline with the bounded multi-agent workflow on the same fault-analysis dataset, run:

```powershell
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis --compare-strategies
```

This calls both workflows for every example, so expect approximately twice the model calls of one run. It writes one comparison JSON plus a separate review bundle for each strategy under `.data/evaluation_results/`. In the Web app's **人工评测** page, select the comparison JSON under **多 Agent 工作流对比** to view strategy metrics, paired deltas, branch-structure metrics, latency, and budget exhaustion. The JSON is parsed in the browser and is not uploaded. Use the per-strategy review bundles with the existing server-backed manual review flow.

Each run writes a metric summary and a separate `*-review.json` bundle under `.data/evaluation_results/`. In the Web app's **人工评测** page, import that bundle, inspect each claim's captured source excerpt and locator, assign evidence labels, select retrieval gold IDs from the captured evidence inventory, provide a local reviewer identifier, and save. The API stores UTC timestamps and append-only versions in SQLite. Re-reviewing creates a new version and preserves prior labels.

The built-in JSONL records are synthetic and unreviewed. Empty gold sets and missing manual labels produce `null` metrics and `insufficient_labels`; the sample-count fields show what was actually scored. A local reviewer identifier is free text and is not an authenticated account. Human labels describe review of the captured output; they do not validate field facts or maintenance outcomes. Do not use these workflows for unauthorized sensitive data or as a substitute for site procedures.

Paired reports include schema pass rate, citation presence/integrity, timing, agent lifecycle counts, and deterministic hypothesis-branch structure checks. Evidence-ID integrity only checks that cited IDs resolve within the captured evidence inventory. Without human gold labels, retrieval recall, semantic evidence support, and human-labeled branch quality remain unavailable; do not treat model confidence or synthetic fixture results as quality accuracy.

## Work-order assistance

The **工单研判** page uses a package-local synthetic fixture through read-only endpoints under `/api/work-orders`. Selecting a row and starting analysis maps the ticket's alarm code, symptom, equipment model, available operating conditions, attachments, and missing-field list into the existing fault-analysis task. Task-to-ticket links and review history are persisted in SQLite. Review versions are append-only and contain a free-text local reviewer label, verdict, note, and timestamp.

This fixture does not connect to a CMMS/ERP, telemetry source, or manufacturer system. Do not interpret its tickets as incidents or review verdicts as externally executed actions. Replacing the fixture with an authorized connector requires a separate integration design covering credentials, field mapping, paging/rate limits, audit, and data retention. The application does not write device parameters or replace equipment manuals, qualified engineering judgment, or site safety procedures.

## CI and scope roadmap

The GitHub Actions `Product CI` workflow runs deterministic product tests, product Ruff checks, frontend tests/build, legacy test collection, and Docker image builds without provider secrets. The legacy collection job proves tests are discoverable; it does not claim the credentialed live upstream evaluations passed. Run the local product checks with:

```powershell
uv run ruff check src/converter_copilot tests/converter_copilot
uv run pytest tests/converter_copilot -q
npm --prefix web test -- --run
npm --prefix web run build
docker compose config
```

Potential later work includes read-only enterprise ticket or telemetry connectors and scheduled analysis with explicit operator review. Authentication, team roles, and multi-tenancy require a separate threat model, storage isolation design, and authorization review. Device write/control, automatic parameter dispatch, and replacing site safety procedures remain outside this application's scope.
