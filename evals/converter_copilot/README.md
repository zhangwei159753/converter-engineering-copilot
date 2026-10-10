# Converter Copilot evaluation

Run a repeatable evaluation against the curated JSONL examples:

```powershell
uv run python evals/converter_copilot/run_eval.py
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis
```

The runner requires `OPENAI_API_KEY` because it executes the selected LangGraph workflows. Results are written as timestamped JSON files under `.data/evaluation_results/`. Each row is isolated: a failed example is recorded with its ID and error while later rows continue. The output includes report status, evidence and claim counts, elapsed time, schema pass rate, citation coverage, and failures. Cost remains `null` until provider usage and pricing data can be measured reliably.

## Metrics and labels

- `retrieval_recall_at_k` is macro Recall@K over non-empty `expected_retrieval_ids` labels.
- `citation_coverage` is the share of generated claims with at least one evidence ID.
- `schema_pass_rate` is the share of examples that returned a valid, mode-matching `ConverterReport`.
- `manual_evidence_support_rate` uses only `supported` and `unsupported` labels. `unverified` and missing labels are excluded.
- Latency is measured locally around each graph invocation. It is not a provider-side latency metric.

The included examples are starter scenarios, not a validated benchmark. They intentionally have empty retrieval gold labels and `unverified` evidence labels, so recall and manual support rate are reported as `null` until a reviewer labels the target evidence. Do not replace these with guessed labels or present starter numbers as quality claims. Add one JSON object per line; each record needs a unique `example_id`, typed workflow `inputs`, an `expected_retrieval_ids` list, and optionally `manual_evidence_labels`.

## Optional LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in the API/evaluation process environment. Tracing remains off unless explicitly requested and a key is present. Workflow runs carry the task ID and mode as tags and metadata. This harness does not attach uploaded document contents, API keys, or secrets to its own result JSON. LangSmith receives the workflow prompts and model inputs when tracing is enabled; use only data approved for that external service.

Run deterministic metric tests without provider keys:

```powershell
uv run pytest tests/converter_copilot/test_eval_metrics.py tests/converter_copilot/test_eval_runner.py -q
```
