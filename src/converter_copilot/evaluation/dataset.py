"""Typed and privacy-safe validation for converter evaluation datasets."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from converter_copilot.domain.schemas import TaskCreate


@dataclass(frozen=True)
class DatasetIssue:
    """One validation issue with its source row and field, never source text."""

    example_id: str | None
    line_number: int | None
    field: str
    message: str


def validate_dataset(records: Sequence[Mapping[str, object]], *, mode: str) -> list[DatasetIssue]:
    """Validate IDs, typed inputs, source/label status, and reviewer metadata."""
    issues: list[DatasetIssue] = []
    seen_ids: set[str] = set()
    for line_number, record in enumerate(records, start=1):
        raw_id = record.get("example_id")
        example_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None
        if example_id is None:
            issues.append(DatasetIssue(None, line_number, "example_id", "Example ID must be a non-empty string."))
        elif example_id in seen_ids:
            issues.append(DatasetIssue(example_id, line_number, "example_id", "Example ID is duplicated."))
        else:
            seen_ids.add(example_id)

        if not isinstance(record.get("source_type"), str) or not str(record.get("source_type", "")).strip():
            issues.append(DatasetIssue(example_id, line_number, "source_type", "Source type must be a non-empty string."))
        label_status = record.get("label_status")
        if not isinstance(label_status, str) or label_status not in {"unreviewed", "reviewed"}:
            issues.append(DatasetIssue(example_id, line_number, "label_status", "Label status must be unreviewed or reviewed."))

        try:
            TaskCreate.model_validate({"mode": mode, "inputs": record.get("inputs")})
        except Exception:
            issues.append(DatasetIssue(example_id, line_number, "inputs", "Inputs do not match the selected workflow mode."))

        expected_ids = record.get("expected_retrieval_ids", [])
        if not isinstance(expected_ids, list) or any(not isinstance(item, str) or not item for item in expected_ids):
            issues.append(DatasetIssue(example_id, line_number, "expected_retrieval_ids", "Expected retrieval IDs must be a list of non-empty strings."))

        evidence_labels = record.get("manual_evidence_labels", [])
        if not isinstance(evidence_labels, list):
            issues.append(DatasetIssue(example_id, line_number, "manual_evidence_labels", "Evidence labels must be a list."))
        else:
            for label in evidence_labels:
                if not isinstance(label, Mapping) or not all(
                    isinstance(label.get(key), str) and label.get(key)
                    for key in ("evidence_id", "claim_id")
                ) or (not isinstance(label.get("label"), str) or label.get("label") not in {"supported", "unsupported", "unverified"}):
                    issues.append(DatasetIssue(example_id, line_number, "manual_evidence_labels", "Evidence label has an invalid shape or value."))
                    break

        if label_status == "reviewed":
            reviewer = record.get("reviewer_id")
            if not isinstance(reviewer, str) or not reviewer.strip():
                issues.append(DatasetIssue(example_id, line_number, "reviewer_id", "Reviewed labels require reviewer attribution."))
            reviewed_at = record.get("reviewed_at")
            if not isinstance(reviewed_at, str) or not _is_datetime(reviewed_at):
                issues.append(DatasetIssue(example_id, line_number, "reviewed_at", "Reviewed labels require an ISO timestamp."))
            label_version = record.get("label_version")
            if not isinstance(label_version, int) or label_version < 1:
                issues.append(DatasetIssue(example_id, line_number, "label_version", "Reviewed labels require a positive label version."))
    return issues


def _is_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None
