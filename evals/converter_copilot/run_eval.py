"""Run curated converter workflow evaluations and save JSON summaries."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from converter_copilot.agent.entrypoint import build_default_deps
from converter_copilot.agent.workflows import build_workflow
from converter_copilot.config import RuntimeConfig
from converter_copilot.domain.schemas import TaskMode
from converter_copilot.evaluation.dataset import validate_dataset
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


def _dataset_paths(modes: list[str], custom_dataset: str | Path | None = None) -> dict[str, Path]:
    """Resolve the default or one explicitly selected workflow dataset."""
    if custom_dataset is not None:
        if len(modes) != 1:
            raise ValueError("--dataset can only be used with one workflow mode.")
        return {modes[0]: Path(custom_dataset).expanduser().resolve()}
    return {mode: (HERE / f"{mode}.jsonl").resolve() for mode in modes}


def _dataset_label(path: Path) -> str:
    """Prefer a repository-relative dataset label when the file is in the project."""
    repository_root = HERE.parent.parent.resolve()
    try:
        return path.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        return str(path.resolve())


async def _run(
    modes: list[str],
    output_dir: Path,
    recall_at_k: int,
    dataset_paths: dict[str, Path] | None = None,
    compare_strategies: bool = False,
) -> list[dict]:
    config = RuntimeConfig.from_env()
    if not config.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for live evaluation. Add it to .env or the environment.")
    config.apply_tracing_environment()
    deps = build_default_deps()
    summaries = []
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dataset_paths = dataset_paths or _dataset_paths(modes)
    for mode_name in modes:
        mode = TaskMode(mode_name)
        dataset = dataset_paths[mode.value]
        records = _load_jsonl(dataset)
        issues = validate_dataset(records, mode=mode.value)
        if issues:
            issue = issues[0]
            raise ValueError(f"{dataset.name}:{issue.line_number}:{issue.field}: {issue.message}")
        dataset_version = hashlib.sha256(dataset.read_bytes()).hexdigest()[:16]
        if compare_strategies and mode != TaskMode.FAULT_ANALYSIS:
            raise ValueError("--compare-strategies currently supports fault_analysis only.")
        if compare_strategies:
            strategy_graphs = {
                strategy: build_workflow(mode, deps, agent_strategy=strategy)
                for strategy in ("baseline", "collaborative")
            }
            summary = await evaluate_records(
                mode=mode.value, records=records, graph=strategy_graphs["baseline"],
                strategy_graphs=strategy_graphs, recall_at_k=recall_at_k, dataset_version=dataset_version,
            )
        else:
            graph = build_workflow(mode, deps)
            summary = await evaluate_records(
                mode=mode.value, records=records, graph=graph, recall_at_k=recall_at_k,
                dataset_version=dataset_version, agent_strategy="collaborative" if mode == TaskMode.FAULT_ANALYSIS else None,
            )
        summary["dataset"] = _dataset_label(dataset)
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        summary["tracing_enabled"] = config.tracing_enabled
        if compare_strategies:
            for strategy, result in summary["strategies"].items():
                review_bundle = result.pop("review_bundle")
                bundle_target = output_dir / f"{mode.value}-{strategy}-{stamp}-review.json"
                bundle_target.write_text(json.dumps(review_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
                result["review_bundle_file"] = str(bundle_target)
        else:
            review_bundle = summary.pop("review_bundle")
            bundle_target = output_dir / f"{mode.value}-{stamp}-review.json"
            bundle_target.write_text(json.dumps(review_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["review_bundle_file"] = str(bundle_target)
        target = output_dir / f"{mode.value}-{stamp}.json"
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["result_file"] = str(target)
        summaries.append(summary)
    return summaries


def main() -> int:
    """Validate datasets, run selected workflows, and write local artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[TaskMode.FAULT_ANALYSIS.value, TaskMode.TECHNICAL_RESEARCH.value], action="append")
    parser.add_argument("--dataset", type=Path, help="Use a specific JSONL dataset; requires exactly one --mode (defaults to fault_analysis).")
    parser.add_argument("--output-dir", type=Path, default=Path(".data/evaluation_results"))
    parser.add_argument("--recall-at-k", type=int, default=5)
    parser.add_argument("--compare-strategies", action="store_true", help="Run baseline and collaborative fault workflows on the same dataset (two workflow runs per example).")
    args = parser.parse_args()
    if args.recall_at_k < 1:
        parser.error("--recall-at-k must be at least 1")
    modes = args.mode or (
        [TaskMode.FAULT_ANALYSIS.value]
        if args.dataset is not None
        else [TaskMode.FAULT_ANALYSIS.value, TaskMode.TECHNICAL_RESEARCH.value]
    )
    if args.compare_strategies and modes != [TaskMode.FAULT_ANALYSIS.value]:
        parser.error("--compare-strategies requires exactly one --mode fault_analysis.")
    try:
        dataset_paths = _dataset_paths(modes, args.dataset)
        results = asyncio.run(_run(modes, args.output_dir, args.recall_at_k, dataset_paths, args.compare_strategies))
    except (RuntimeError, ValueError) as error:
        parser.exit(2, f"Evaluation stopped: {error}\n")
    sys.stdout.write(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
