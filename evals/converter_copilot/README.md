# Converter Copilot evaluation

Run a repeatable evaluation against the curated JSONL examples:

```powershell
uv run python evals/converter_copilot/run_eval.py
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis
```

## Developer-authored synthetic fault-quality pack

`synthetic_fault_quality_v1.jsonl` contains 20 deliberately constructed scenarios: 4 direct matches, 4 cross-equipment distractors, 4 missing-information cases, 4 conflicting-fixture cases, and 4 no-relevant-evidence cases. `synthetic_converter_evidence_v1.csv` is the matching 12-row import corpus. Both files are synthetic test fixtures, not manufacturer materials, field history, expert-reviewed labels, or validated engineering conclusions.

The pack keeps `label_status` as `unreviewed`, and both retrieval gold IDs and evidence labels empty. `expected_behavior` and `target_case_keys` are scenario authoring notes, not model inputs or scored gold labels. Run a live workflow evaluation with:

```powershell
uv run python evals/converter_copilot/run_eval.py `
  --mode fault_analysis `
  --dataset evals/converter_copilot/synthetic_fault_quality_v1.jsonl `
  --output-dir .data/evaluation_results/synthetic_fault_quality_v1
```

For a reproducible isolated run, set these same environment values in the terminal used to start the Web/API process and the terminal used for the CLI:

```powershell
$env:CHROMA_PERSIST_DIR = ".data/synthetic_eval_v1/chroma"
$env:CHROMA_COLLECTION_NAME = "converter_copilot_synthetic_fault_quality_v1"
$env:CONVERTER_DB_PATH = ".data/synthetic_eval_v1/reviews.db"
```

Start the Web/API process with those values, import `synthetic_converter_evidence_v1.csv` through the Web UI, then run the command above in a second terminal after setting the same values. The CLI writes an evaluation summary and review bundle; import the `*-review.json` bundle from **人工评测** and manually inspect each excerpt against the `expected_behavior` and `target_case_keys` notes in the JSONL. Select expected retrieval IDs and label claim/evidence pairs only after reviewing the actual snapshots. The UI reviewer ID records who made that local label; it does not certify engineering expertise. Review labels are retained in the local review ledger; the starter JSONL stays unreviewed, and later bundle imports are tied to a dataset version hash.

`--dataset` accepts one JSONL file and therefore requires exactly one workflow mode. When it is supplied without `--mode`, the CLI defaults to `fault_analysis`. The default command without `--dataset` continues to use the repository's standard per-mode datasets.

This pack tests workflow behavior against intentionally controlled fixtures. Its results must be reported as synthetic regression results, not as field diagnostic accuracy or expert validation. Do not use these fixtures for equipment operation or safety decisions.

The runner requires `DEEPSEEK_API_KEY` because it executes the selected LangGraph workflows. Local document retrieval uses FastEmbed and the `BAAI/bge-small-zh-v1.5` model, downloaded on first use. Results are written as timestamped JSON files under `.data/evaluation_results/`. Each row is isolated: a failed example is recorded with its ID and error while later rows continue. The output includes report status, evidence and claim counts, elapsed time, schema pass rate, citation coverage, and failures. Cost remains `null` until provider usage and pricing data can be measured reliably.

## Metrics and labels

- `retrieval_recall_at_k` is macro Recall@K over non-empty `expected_retrieval_ids` labels.
- `citation_coverage` is the share of generated claims with at least one evidence ID. It measures citation presence only; it does not measure citation correctness or claim support. Read it alongside each outcome's `claim_count`, since abstaining from unsupported claims can change the denominator.
- `citation_integrity_rate` is the share of cited claims whose evidence IDs resolve to unique inventory records with usable source metadata. This is a deterministic link check, not a semantic entailment check.
- `schema_pass_rate` is the share of examples that returned a valid, mode-matching `ConverterReport`.
- `manual_evidence_support_rate` uses only `supported` and `unsupported` labels. `unverified` and missing labels are excluded.
- A report claim marked `supported` means its evidence links and source records passed structural checks. Whether the excerpt directly supports the claim is a separate human judgment captured in `manual_evidence_support_rate`.
- Latency is measured locally around each graph invocation. It is not a provider-side latency metric.

The included examples are synthetic starter scenarios, not a validated benchmark. They have `label_status: "unreviewed"`, empty retrieval gold labels, and no evidence labels, so recall and manual support rate are reported as `null` with `insufficient_labels`. The CLI validates every row and stops with the file, line, and field for malformed data. Validation messages do not echo source text.

Each live run writes a separate `*-review.json` bundle next to the metric summary. Import that bundle from the Web application's **人工评测** page. The review queue stores the dataset version, generated claims, evidence excerpts and locators, and immutable snapshots in SQLite. A reviewer can mark each evidence/claim pair as `supported`, `unsupported`, or `unverified`, and select expected retrieval IDs from the snapshot inventory.

Saving a review requires a nonblank local reviewer identifier. The server records the UTC review time and increments `label_version`; later edits append another event and preserve earlier labels in history. This identifier is free-text attribution in this single-user local app, not a verified account or authorization identity. Do not import files containing unauthorized or sensitive field data.

Only explicit human reviews contribute to retrieval and semantic evidence-support metrics. `metric_sample_counts` reports eligible examples, labels, and claim counts; `metric_status` is `insufficient_labels` and the metric is `null` when no eligible labels exist. The API summary computes retrieval recall over reviewed non-empty gold sets and the captured evidence inventory. Build a named/versioned benchmark baseline only after reviewing the dataset, recording its source and reviewer process, and rerunning it against a fixed model/configuration. A human evidence label records a reviewer judgment about a snapshot; it does not establish field correctness or maintenance benefit.

## Optional LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in the API/evaluation process environment. Tracing remains off unless explicitly requested and a key is present. Workflow runs carry the task ID and mode as tags and metadata. This harness does not attach uploaded document contents, API keys, or secrets to its own result JSON. LangSmith receives the workflow prompts and model inputs when tracing is enabled; use only data approved for that external service.

Run deterministic metric tests without provider keys:

```powershell
uv run pytest tests/converter_copilot/test_eval_metrics.py tests/converter_copilot/test_eval_runner.py -q
```

## Multi-agent strategy comparison

Compare the retained single-workflow baseline with bounded collaborative fault analysis on exactly the same records:

```powershell
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis --compare-strategies
```
The comparison executes both workflows for each record and makes about twice as many model calls. It writes paired per-example outcomes, metric deltas, branch evidence-integrity/coverage/applicability/completion checks, agent timing and lifecycle counts, and one review bundle per strategy. Open the comparison JSON in **人工评测 → 多 Agent 工作流对比**; the browser reads it locally. Import each strategy's `*-review.json` separately to add human evidence labels.

Built-in data remains synthetic and unreviewed. Branch/citation metrics are deterministic workflow-integrity measures, not semantic accuracy. Retrieval recall, evidence-support rate, and human-labeled branch quality stay unavailable until reviewers create the appropriate labels. Provider usage appears only when an adapter supplies usage metadata.
