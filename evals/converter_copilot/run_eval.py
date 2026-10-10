"""Run curated converter workflow evaluations and save JSON summaries."""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from converter_copilot.agent.entrypoint import build_default_deps
from converter_copilot.agent.workflows import build_workflow
from converter_copilot.config import RuntimeConfig
from converter_copilot.domain.schemas import TaskMode
from converter_copilot.evaluation.runner import evaluate_records


HERE = Path(__file__).resolve().parent


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON in {path.name}:{line_number}: {error.msg}") from error
        if not isinstance(record, dict) or "inputs" not in record:
            raise ValueError(f"Each JSONL row must be an object with inputs ({path.name}:{line_number}).")
        records.append(record)
    return records


async def _run(modes: list[str], output_dir: Path, recall_at_k: int) -> list[dict]:
    config = RuntimeConfig.from_env()
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for live evaluation. Add it to .env or the environment.")
    config.apply_tracing_environment()
    deps = build_default_deps()
    summaries = []
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for mode_name in modes:
        mode = TaskMode(mode_name)
        dataset = HERE / f"{mode.value}.jsonl"
        records = _load_jsonl(dataset)
        graph = build_workflow(mode, deps)
        summary = await evaluate_records(mode=mode.value, records=records, graph=graph, recall_at_k=recall_at_k)
        summary["dataset"] = str(dataset.relative_to(HERE.parent.parent))
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        summary["tracing_enabled"] = config.tracing_enabled
        target = output_dir / f"{mode.value}-{stamp}.json"
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["result_file"] = str(target)
        summaries.append(summary)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[TaskMode.FAULT_ANALYSIS.value, TaskMode.TECHNICAL_RESEARCH.value], action="append")
    parser.add_argument("--output-dir", type=Path, default=Path(".data/evaluation_results"))
    parser.add_argument("--recall-at-k", type=int, default=5)
    args = parser.parse_args()
    if args.recall_at_k < 1:
        parser.error("--recall-at-k must be at least 1")
    modes = args.mode or [TaskMode.FAULT_ANALYSIS.value, TaskMode.TECHNICAL_RESEARCH.value]
    try:
        results = asyncio.run(_run(modes, args.output_dir, args.recall_at_k))
    except (RuntimeError, ValueError) as error:
        parser.exit(2, f"Evaluation stopped: {error}\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
