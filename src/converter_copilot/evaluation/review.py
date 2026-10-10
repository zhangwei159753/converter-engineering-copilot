"""Local human review queue and append-only evaluation label history."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from converter_copilot.domain.schemas import TaskCreate, TaskMode
from converter_copilot.storage.sqlite_store import SQLiteStore


class EvidenceLabel(BaseModel):
    """One human judgment for an evidence/claim pair."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    label: Literal["supported", "unsupported", "unverified"]


class ReviewBundleExample(BaseModel):
    """Immutable model output snapshot carried by a review bundle."""

    model_config = ConfigDict(extra="forbid")

    example_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    inputs: dict[str, object]
    task_id: str = Field(min_length=1)
    claims: list[dict[str, object]] = Field(default_factory=list)
    evidence: list[dict[str, object]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_snapshot_ids(self):
        """Require unique identifiers so labels can target a stable snapshot."""
        claim_ids = [item.get("claim_id") for item in self.claims]
        evidence_ids = [item.get("evidence_id") for item in self.evidence]
        if any(not isinstance(item, str) or not item for item in claim_ids + evidence_ids):
            raise ValueError("Claim and evidence snapshots require IDs.")
        if len(claim_ids) != len(set(claim_ids)) or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Claim and evidence snapshot IDs must be unique.")
        evidence_inventory = set(evidence_ids)
        for claim in self.claims:
            referenced = claim.get("evidence_ids", [])
            if not isinstance(referenced, list) or any(not isinstance(item, str) for item in referenced):
                raise ValueError("Claim snapshots must reference evidence in the same bundle.")
            if set(referenced) - evidence_inventory:
                raise ValueError("Claim snapshots must reference evidence in the same bundle.")
        return self


class EvaluationReviewBundle(BaseModel):
    """Versioned collection of examples prepared for human review."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str = Field(min_length=1)
    mode: Literal["fault_analysis", "technical_research"]
    dataset_version: str = Field(min_length=1)
    generated_at: datetime
    examples: list[ReviewBundleExample] = Field(min_length=1)

    @field_validator("examples")
    @classmethod
    def unique_examples(cls, examples):
        """Reject duplicate example IDs before the bundle is persisted."""
        ids = [example.example_id for example in examples]
        if len(ids) != len(set(ids)):
            raise ValueError("Example IDs must be unique within a review bundle.")
        return examples

    @model_validator(mode="after")
    def validate_mode_inputs(self):
        """Validate each captured request against its declared workflow mode."""
        for example in self.examples:
            TaskCreate.model_validate({"mode": self.mode, "inputs": example.inputs})
        return self


class BundleImportResult(BaseModel):
    """Safe summary of a review bundle import or idempotent reuse."""

    bundle_id: str
    imported_count: int
    skipped_count: int
    reused: bool = False


class EvaluationReview(BaseModel):
    """One immutable review event returned from the audit history."""

    review_id: str
    label_version: int
    reviewer_id: str
    reviewed_at: datetime
    evidence_labels: list[EvidenceLabel]
    expected_retrieval_ids: list[str]
    note: str | None = None


class EvaluationExample(BaseModel):
    """Review queue row with immutable inputs and the current label projection."""

    review_id: str
    bundle_id: str
    example_id: str
    mode: str
    dataset_version: str
    source_type: str
    inputs: dict[str, object]
    task_id: str
    claims: list[dict[str, object]]
    evidence: list[dict[str, object]]
    status: Literal["pending", "reviewed"]
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    label_version: int
    evidence_labels: list[EvidenceLabel]
    expected_retrieval_ids: list[str]
    note: str | None = None


class EvaluationReviewService:
    """Persist review snapshots and label revisions in the local SQLite database."""

    def __init__(self, store: SQLiteStore):
        """Bind the service to the app's local SQLite store."""
        self.store = store

    def import_review_bundle(self, bundle: EvaluationReviewBundle) -> BundleImportResult:
        """Import a bundle once and preserve its original model snapshots."""
        now = datetime.now(timezone.utc).isoformat()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT imported_count, skipped_count FROM evaluation_bundles WHERE bundle_id = ?",
                (bundle.bundle_id,),
            ).fetchone()
            if existing:
                return BundleImportResult(
                    bundle_id=bundle.bundle_id,
                    imported_count=existing["imported_count"],
                    skipped_count=existing["skipped_count"],
                    reused=True,
                )
            connection.execute(
                """INSERT INTO evaluation_bundles(bundle_id, mode, dataset_version, generated_at,
                imported_count, skipped_count, imported_at) VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (bundle.bundle_id, bundle.mode, bundle.dataset_version, bundle.generated_at.isoformat(), len(bundle.examples), now),
            )
            for item in bundle.examples:
                connection.execute(
                    """INSERT INTO evaluation_examples(review_id, bundle_id, example_id, mode, source_type,
                    inputs_json, task_id, claims_json, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid4()), bundle.bundle_id, item.example_id, bundle.mode, item.source_type,
                        _json(item.inputs), item.task_id, _json(item.claims), _json(item.evidence),
                    ),
                )
        return BundleImportResult(bundle_id=bundle.bundle_id, imported_count=len(bundle.examples), skipped_count=0)

    def list_examples(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EvaluationExample], int]:
        """Return filtered, bounded review rows and the matching total count."""
        if mode is not None and mode not in {item.value for item in TaskMode}:
            raise ValueError("Unsupported evaluation mode.")
        if status is not None and status not in {"pending", "reviewed"}:
            raise ValueError("Status must be pending or reviewed.")
        if source_type is not None and not source_type.strip():
            raise ValueError("Source type filter cannot be blank.")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Pagination is out of range.")
        clauses, values = [], []
        if mode:
            clauses.append("e.mode = ?")
            values.append(mode)
        if status:
            clauses.append("e.status = ?")
            values.append(status)
        if source_type:
            clauses.append("e.source_type = ?")
            values.append(source_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store._connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM evaluation_examples e {where}", values).fetchone()[0]
            rows = connection.execute(
                f"""SELECT e.*, b.dataset_version FROM evaluation_examples e
                JOIN evaluation_bundles b ON b.bundle_id = e.bundle_id {where}
                ORDER BY e.example_id, e.review_id LIMIT ? OFFSET ?""",
                [*values, limit, offset],
            ).fetchall()
        return [self._example_from_row(row) for row in rows], int(total)

    def get_example(self, review_id: str) -> EvaluationExample | None:
        """Return one example including its current review projection."""
        with self.store._connect() as connection:
            row = connection.execute(
                """SELECT e.*, b.dataset_version FROM evaluation_examples e
                JOIN evaluation_bundles b ON b.bundle_id = e.bundle_id WHERE e.review_id = ?""",
                (review_id,),
            ).fetchone()
        return self._example_from_row(row) if row else None

    def save_review(
        self,
        review_id: str,
        *,
        reviewer_id: str,
        evidence_labels: list[EvidenceLabel],
        expected_retrieval_ids: list[str],
        note: str | None = None,
    ) -> EvaluationReview:
        """Append a validated label event and update the current projection atomically."""
        reviewer_id = reviewer_id.strip()
        if not reviewer_id or len(reviewer_id) > 120:
            raise ValueError("Reviewer attribution must contain 1 to 120 characters.")
        if len(expected_retrieval_ids) != len(set(expected_retrieval_ids)) or any(not item for item in expected_retrieval_ids):
            raise ValueError("Expected retrieval IDs must be unique non-empty strings.")
        if note is not None and len(note) > 2000:
            raise ValueError("Review note exceeds 2000 characters.")
        timestamp = datetime.now(timezone.utc)
        labels_json = _json([item.model_dump(mode="json") for item in evidence_labels])
        retrieval_json = _json(expected_retrieval_ids)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM evaluation_examples WHERE review_id = ?", (review_id,)).fetchone()
            if row is None:
                raise KeyError(review_id)
            claim_snapshots = {item["claim_id"]: item for item in json.loads(row["claims_json"])}
            evidence_snapshots = {item["evidence_id"]: item for item in json.loads(row["evidence_json"])}
            claims = set(claim_snapshots)
            evidence = set(evidence_snapshots)
            valid_retrieval_ids = evidence
            seen_pairs = set()
            for label in evidence_labels:
                pair = (label.evidence_id, label.claim_id)
                if pair in seen_pairs:
                    raise ValueError("Evidence labels cannot repeat the same evidence/claim pair.")
                seen_pairs.add(pair)
                if label.evidence_id not in evidence or label.claim_id not in claims:
                    raise ValueError("Evidence label references an unknown snapshot ID.")
                if label.evidence_id not in claim_snapshots[label.claim_id].get("evidence_ids", []):
                    raise ValueError("Evidence label must reference evidence linked to the selected claim.")
            if set(expected_retrieval_ids) - valid_retrieval_ids:
                raise ValueError("Expected retrieval IDs must reference this example's snapshot inventory.")
            version = int(row["label_version"]) + 1
            connection.execute(
                """INSERT INTO evaluation_review_events(review_id, label_version, reviewer_id, reviewed_at,
                evidence_labels_json, expected_retrieval_ids_json, note) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (review_id, version, reviewer_id, timestamp.isoformat(), labels_json, retrieval_json, note),
            )
            connection.execute(
                """UPDATE evaluation_examples SET status = 'reviewed', reviewer_id = ?, reviewed_at = ?,
                label_version = ?, evidence_labels_json = ?, expected_retrieval_ids_json = ?, note = ?
                WHERE review_id = ?""",
                (reviewer_id, timestamp.isoformat(), version, labels_json, retrieval_json, note, review_id),
            )
        return EvaluationReview(
            review_id=review_id, label_version=version, reviewer_id=reviewer_id, reviewed_at=timestamp,
            evidence_labels=evidence_labels, expected_retrieval_ids=expected_retrieval_ids, note=note,
        )

    def history(self, review_id: str) -> list[EvaluationReview]:
        """Return all immutable label revisions in submission order."""
        with self.store._connect() as connection:
            exists = connection.execute("SELECT 1 FROM evaluation_examples WHERE review_id = ?", (review_id,)).fetchone()
            if not exists:
                raise KeyError(review_id)
            rows = connection.execute(
                "SELECT * FROM evaluation_review_events WHERE review_id = ? ORDER BY event_id", (review_id,)
            ).fetchall()
        return [
            EvaluationReview(
                review_id=row["review_id"], label_version=row["label_version"], reviewer_id=row["reviewer_id"],
                reviewed_at=row["reviewed_at"], evidence_labels=json.loads(row["evidence_labels_json"]),
                expected_retrieval_ids=json.loads(row["expected_retrieval_ids_json"]), note=row["note"],
            ) for row in rows
        ]

    def summary(self) -> dict[str, object]:
        """Aggregate review counts and quality metrics from eligible labels only."""
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT mode, source_type, status, COUNT(*) AS count FROM evaluation_examples GROUP BY mode, source_type, status"
            ).fetchall()
            versions = connection.execute(
                "SELECT DISTINCT mode, dataset_version FROM evaluation_bundles ORDER BY mode, dataset_version"
            ).fetchall()
            reviewed = connection.execute(
                """SELECT evidence_json, evidence_labels_json, expected_retrieval_ids_json
                FROM evaluation_examples WHERE status = 'reviewed'"""
            ).fetchall()
        modes: dict[str, dict[str, object]] = {}
        for row in rows:
            mode = modes.setdefault(row["mode"], {"total": 0, "pending": 0, "reviewed": 0, "source_types": {}})
            mode["total"] = int(mode["total"]) + row["count"]
            mode[row["status"]] = int(mode[row["status"]]) + row["count"]
            source_types = mode["source_types"]
            source_types[row["source_type"]] = source_types.get(row["source_type"], 0) + row["count"]
        retrieval_scores = []
        support_labels = []
        for row in reviewed:
            expected = set(json.loads(row["expected_retrieval_ids_json"]))
            if expected:
                retrieved = [item["evidence_id"] for item in json.loads(row["evidence_json"])][:5]
                retrieval_scores.append(len(expected & set(retrieved)) / len(expected))
            support_labels.extend(
                item["label"] for item in json.loads(row["evidence_labels_json"])
                if item["label"] in {"supported", "unsupported"}
            )
        retrieval_metric = sum(retrieval_scores) / len(retrieval_scores) if retrieval_scores else None
        support_metric = (
            sum(label == "supported" for label in support_labels) / len(support_labels)
            if support_labels else None
        )
        metric_status = {
            "retrieval_recall_at_k": "measured" if retrieval_metric is not None else "insufficient_labels",
            "manual_evidence_support_rate": "measured" if support_metric is not None else "insufficient_labels",
        }
        return {
            "modes": modes,
            "dataset_versions": [dict(row) for row in versions],
            "retrieval_recall_at_k": retrieval_metric,
            "manual_evidence_support_rate": support_metric,
            "metric_status": metric_status,
            "metric_sample_counts": {
                "retrieval_labeled_examples": len(retrieval_scores),
                "reviewed_evidence_labels": len(support_labels),
            },
            "metric_reasons": {
                key: "No eligible human-reviewed labels are available."
                for key, value in metric_status.items() if value == "insufficient_labels"
            },
        }

    @staticmethod
    def _example_from_row(row: sqlite3.Row) -> EvaluationExample:
        return EvaluationExample(
            review_id=row["review_id"], bundle_id=row["bundle_id"], example_id=row["example_id"],
            mode=row["mode"], dataset_version=row["dataset_version"], source_type=row["source_type"],
            inputs=json.loads(row["inputs_json"]), task_id=row["task_id"], claims=json.loads(row["claims_json"]),
            evidence=json.loads(row["evidence_json"]), status=row["status"], reviewer_id=row["reviewer_id"],
            reviewed_at=row["reviewed_at"], label_version=row["label_version"],
            evidence_labels=json.loads(row["evidence_labels_json"]),
            expected_retrieval_ids=json.loads(row["expected_retrieval_ids_json"]), note=row["note"],
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
