# Converter Copilot evaluation

Run a repeatable evaluation against the curated JSONL examples:

```powershell
uv run python evals/converter_copilot/run_eval.py
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis
```

The runner requires `DEEPSEEK_API_KEY` because it executes the selected LangGraph workflows. Local document retrieval uses FastEmbed and the `BAAI/bge-small-zh-v1.5` model, downloaded on first use. Results are written as timestamped JSON files under `.data/evaluation_results/`. Each row is isolated: a failed example is recorded with its ID and error while later rows continue. The output includes report status, evidence and claim counts, elapsed time, schema pass rate, citation coverage, and failures. Cost remains `null` until provider usage and pricing data can be measured reliably.

## Metrics and labels

- `retrieval_recall_at_k` is macro Recall@K over non-empty `expected_retrieval_ids` labels.
- `citation_coverage` is the share of generated claims with at least one evidence ID.
- `schema_pass_rate` is the share of examples that returned a valid, mode-matching `ConverterReport`.
- `manual_evidence_support_rate` uses only `supported` and `unsupported` labels. `unverified` and missing labels are excluded.
- Latency is measured locally around each graph invocation. It is not a provider-side latency metric.

The included examples are synthetic starter scenarios, not a validated benchmark. They have `label_status: "unreviewed"`, empty retrieval gold labels, and no evidence labels, so recall and manual support rate are reported as `null` with `insufficient_labels`. The CLI validates every row and stops with the file, line, and field for malformed data. Validation messages do not echo source text.

Each live run writes a separate `*-review.json` bundle next to the metric summary. Import that bundle from the Web application's **人工评测** page. The review queue stores the dataset version, generated claims, evidence excerpts and locators, and immutable snapshots in SQLite. A reviewer can mark each evidence/claim pair as `supported`, `unsupported`, or `unverified`, and select expected retrieval IDs from the snapshot inventory.

Saving a review requires a nonblank local reviewer identifier. The server records the UTC review time and increments `label_version`; later edits append another event and preserve earlier labels in history. This identifier is free-text attribution in this single-user local app, not a verified account or authorization identity. Do not import files containing unauthorized or sensitive field data.

Only explicit human reviews contribute to retrieval and evidence-support metrics. `metric_sample_counts` reports eligible examples and labels; `metric_status` is `insufficient_labels` and the metric is `null` when no eligible labels exist. The API summary computes retrieval recall over reviewed non-empty gold sets and the captured evidence inventory. Build a named/versioned benchmark baseline only after reviewing the dataset, recording its source and reviewer process, and rerunning it against a fixed model/configuration. A human evidence label records a reviewer judgment about a snapshot; it does not establish field correctness or maintenance benefit.

## Optional LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in the API/evaluation process environment. Tracing remains off unless explicitly requested and a key is present. Workflow runs carry the task ID and mode as tags and metadata. This harness does not attach uploaded document contents, API keys, or secrets to its own result JSON. LangSmith receives the workflow prompts and model inputs when tracing is enabled; use only data approved for that external service.

Run deterministic metric tests without provider keys:

```powershell
uv run pytest tests/converter_copilot/test_eval_metrics.py tests/converter_copilot/test_eval_runner.py -q
```
