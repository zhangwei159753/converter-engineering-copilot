# Product CI and Local Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the repository a repeatable, no-secret product CI pipeline and local operating documentation for migration, task recovery, imports, and evaluation.

**Architecture:** A dedicated GitHub Actions workflow validates the Python product package and deterministic tests, builds/tests the React app, and builds the Docker image without running live workflows. Pytest discovery keeps legacy tests visible and gives them a separate, explicit result. README and operations docs describe how to run and repair the local single-instance app.

**Tech Stack:** GitHub Actions, Python/uv, pytest, npm, Vitest, TypeScript, Docker BuildKit.

**Spec:** `docs/superpowers/specs/2026-10-10-local-reliability-v1-design.md`

## Global Constraints

- Depend on task, ingestion, and evaluation tests/contracts from the other three plans.
- CI must pass without `DEEPSEEK_API_KEY`, Tavily, or LangSmith secrets and must not call paid models.
- Build the Docker image only; do not deploy it or start a public service.
- Keep product tests independently required; do not silently exclude legacy tests and claim the full suite passed.
- Document the local single-instance storage restriction and state that device control and SOP replacement are out of scope.

## Review Focus

- Missing or malformed model keys in CI: test that deterministic jobs do not inspect or require provider secrets.
- Existing `--research-agent` option: test root pytest invocation can parse it and legacy results are visible.
- Frontend TypeScript error: require production `npm run build` in CI, not only unit tests.
- Docker context/secret leakage: test image build excludes `.env` and `.data` and passes no provider secret build args.
- Documentation drift: validate each documented command against declared project scripts/workflows and confirm roadmap safety boundaries are explicit.

---

### Task 1: Make root pytest discovery explicit and preserve the legacy suite

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/legacy/tests/conftest.py`
- Create: `conftest.py`
- Test: Create `tests/converter_copilot/test_pytest_config.py`

- [x] Add `test_legacy_live_test_collects_with_root_cli_option`, launching `python -m pytest --collect-only src/legacy/tests/test_report_quality.py --research-agent=multi_agent` and asserting collection succeeds.
- [x] Run `uv run pytest tests/converter_copilot/test_pytest_config.py -q`; expected: the subprocess fails because root pytest does not register `--research-agent`.
- [x] Register `--research-agent`, `--search-api`, `--eval-model`, `--supervisor-model`, `--researcher-model`, `--planner-provider`, `--planner-model`, `--writer-provider`, `--writer-model`, and `--max-search-depth` once in a root-loaded `conftest.py`; remove duplicate option registration from `src/legacy/tests/conftest.py` while retaining its fixtures.
- [x] Re-run the subprocess check and `uv run pytest --collect-only tests/converter_copilot src/legacy/tests --research-agent=multi_agent`; expected: product and legacy tests collect and CLI flags parse. Collection is not reported as live test success.
- [x] Commit as `test: make product and legacy pytest discovery explicit`.

### Task 2: Add deterministic backend and migration CI job

**Files:**
- Create: `.github/workflows/product-ci.yml`
- Modify: `pyproject.toml`
- Modify: `uv.lock` only if the chosen CI command requires a declared dev dependency

- [x] Add workflow jobs for Python setup, `uv sync --locked --extra dev`, Ruff checks on product files, product tests, and a separate legacy collection/live-evaluation status. Normal CI collects legacy tests but marks the live Anthropic/Tavily/LangSmith evaluation as not run because credentials are not configured; it does not count collection as a pass of that test.
- [x] Add workflow guard test or YAML review assertion that the product job runs with provider keys unset and invokes no live evaluation command.
- [x] Run local equivalents `uv run ruff check src/converter_copilot tests/converter_copilot` and `uv run pytest tests/converter_copilot -q`; expected: existing product checks establish the workflow command contract.
- [x] Configure GitHub Actions to run those commands on pull requests and pushes to the repository default branch; never use a secret fallback or skip product tests when a model key is absent. Do not require model-provider secrets in CI.
- [x] Validate workflow YAML and command paths locally; expected: CI invokes only declared project scripts and deterministic tests.
- [x] Commit as `ci: add deterministic python product checks`.

### Task 3: Add frontend tests/build and Docker image build

**Files:**
- Modify: `.github/workflows/product-ci.yml`
- Create: `.dockerignore`
- Modify: `web/package.json` only if a stable non-watch test script is needed
- Test: existing `web/src/**/*.test.tsx`

- [ ] Add CI steps `npm ci`, `npm test -- --run`, and `npm run build` from `web/`.
- [ ] Add Docker build job using the repository `Dockerfile`; ensure `.env`, `.data`, and `web/node_modules` are excluded from the build context.
- [ ] Run `npm --prefix web test -- --run` and `npm --prefix web run build`; expected: frontend tests and typechecked production build pass.
- [ ] Run the same Docker build command locally if Docker is available; expected: image build succeeds without provider secrets and does not start/deploy containers.
- [ ] Commit as `ci: build web app and docker image`.

### Task 4: Publish local operations and project roadmap

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/demo-scenario.md`
- Modify: `.env.example`
- Create: `docs/operations.md`

- [ ] Document setup, local single-instance data paths, backup-before-migration, task recovery settings, import validation/repair, evaluation review, health/readiness, and CI checks.
- [ ] Add roadmap boundaries: future read-only enterprise connectors and scheduled analysis may be explored; account/multitenancy requires a separate design; device write/control and SOP replacement remain excluded.
- [ ] Check every shell command against existing `uv`/npm/Docker scripts and ensure no credentials appear in examples.
- [ ] Commit as `docs: add local operations and product roadmap`.
