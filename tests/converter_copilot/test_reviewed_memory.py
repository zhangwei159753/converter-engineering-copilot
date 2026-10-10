from datetime import datetime, timezone

import pytest

from converter_copilot.domain import memory as memory_module
from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    Evidence,
    FaultAnalysisReport,
    TaskCreate,
    TaskMode,
)
from converter_copilot.domain.work_orders import WorkOrderRecord
from converter_copilot.storage.sqlite_store import SQLiteStore


def _work_order(*, synthetic=False):
    return WorkOrderRecord(
        work_order_id="WO-REAL-1",
        equipment_model="CPC-2500 / 690 V",
        serial_number="SERIAL-1",
        alarm_code="DC_OV_017",
        symptom="DC bus overvoltage during regeneration",
        occurred_at="2026-10-08T09:42:00+08:00",
        operating_conditions={"active_power_kw": 1800},
        severity="high",
        status="resolved",
        synthetic_demo=synthetic,
    )


def _report(task_id, *, completion="completed", evidence=True):
    evidence_items = [Evidence(
        evidence_id="ev-real-1",
        task_id=task_id,
        title="Service manual section 4",
        source_type="web",
        excerpt="Regenerative energy must be dissipated by the braking circuit.",
        url="https://example.test/manual",
        accessed_at=datetime.now(timezone.utc),
        source_status="available",
        applicability="direct",
    )] if evidence else []
    claims = [Claim(
        claim_id="claim-1",
        text="Inspect the braking circuit for failure.",
        claim_type="inference",
        evidence_ids=["ev-real-1"] if evidence else [],
        verification_status="supported" if evidence else "unverified",
    )]
    return ConverterReport(
        task_id=task_id,
        mode=TaskMode.FAULT_ANALYSIS,
        result=FaultAnalysisReport(hypotheses=[]),
        claims=claims,
        evidence=evidence_items,
        completion_status=completion,
    )


def _seed_reviewable(store, *, completion="completed", evidence=True, verdict="accepted"):
    task, _ = store.create_task(
        TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={
            "equipment_type": "CPC-2500 / 690 V",
            "symptom": "DC bus overvoltage",
            "operating_conditions": {"active_power_kw": 1800},
        }),
        work_order_id="WO-REAL-1",
    )
    store.update_task_status(task.task_id, completion)
    store.save_report(_report(task.task_id, completion=completion, evidence=evidence))
    review = store.save_work_order_review(
        "WO-REAL-1", task.task_id, reviewer="engineer", verdict=verdict, note="Checked against field record."
    )
    return task.task_id, review


def test_accepted_review_projection_is_idempotent_and_searchable(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "get_work_order", lambda _work_order_id: _work_order())
    store = SQLiteStore(tmp_path / "reviewed-memory.db")
    task_id, review = _seed_reviewable(store)

    count = memory_module.project_accepted_review(store, "WO-REAL-1", task_id, review.version)
    first_edges = store.list_memory_edges()
    repeated = memory_module.project_accepted_review(store, "WO-REAL-1", task_id, review.version)
    matches = memory_module.search_memory(store, query="braking circuit", equipment_type="CPC-2500 / 690 V")

    assert count > 0
    assert repeated == count
    assert store.list_memory_edges() == first_edges
    assert matches[0].source_work_order_id == "WO-REAL-1"
    assert matches[0].source_review_version == review.version
    assert matches[0].evidence_ids == ["ev-real-1"]
    assert matches[0].applicability_differences == []
    assert matches[0].synthetic_demo is False


def test_synthetic_incomplete_and_source_less_reviews_do_not_create_memory(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "excluded-memory.db")
    monkeypatch.setattr(memory_module, "get_work_order", lambda _work_order_id: _work_order(synthetic=True))
    synthetic_task, synthetic_review = _seed_reviewable(store)
    assert memory_module.project_accepted_review(store, "WO-REAL-1", synthetic_task, synthetic_review.version) == 0

    monkeypatch.setattr(memory_module, "get_work_order", lambda _work_order_id: _work_order())
    incomplete_task, incomplete_review = _seed_reviewable(store, completion="partial")
    assert memory_module.project_accepted_review(store, "WO-REAL-1", incomplete_task, incomplete_review.version) == 0

    source_less_task, source_less_review = _seed_reviewable(store, evidence=False)
    assert memory_module.project_accepted_review(store, "WO-REAL-1", source_less_task, source_less_review.version) == 0
    assert store.list_memory_edges() == []


def test_rejected_latest_review_invalidates_previously_accepted_edges(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "get_work_order", lambda _work_order_id: _work_order())
    store = SQLiteStore(tmp_path / "review-revoke.db")
    task_id, accepted = _seed_reviewable(store)
    memory_module.project_accepted_review(store, "WO-REAL-1", task_id, accepted.version)
    rejected = store.save_work_order_review(
        "WO-REAL-1", task_id, reviewer="engineer", verdict="rejected", note="Evidence mismatch."
    )

    assert memory_module.project_accepted_review(store, "WO-REAL-1", task_id, rejected.version) == 0
    assert store.list_memory_edges() == []


def test_memory_search_reports_equipment_difference(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "get_work_order", lambda _work_order_id: _work_order())
    store = SQLiteStore(tmp_path / "memory-differences.db")
    task_id, review = _seed_reviewable(store)
    memory_module.project_accepted_review(store, "WO-REAL-1", task_id, review.version)

    match = memory_module.search_memory(store, equipment_type="CPC-1800 / 690 V")[0]

    assert "Equipment model differs from the reviewed source case." in match.applicability_differences


def test_review_is_rolled_back_if_memory_projection_fails(tmp_path):
    store = SQLiteStore(tmp_path / "review-atomic.db")
    task_id, _ = _seed_reviewable(store, verdict="needs_more_information")

    with pytest.raises(RuntimeError, match="projection failed"):
        store.save_work_order_review(
            "WO-REAL-1",
            task_id,
            reviewer="engineer",
            verdict="accepted",
            note=None,
            projection_factory=lambda _version: (_ for _ in ()).throw(RuntimeError("projection failed")),
        )

    assert [review.verdict for review in store.list_work_order_reviews("WO-REAL-1", task_id)] == [
        "needs_more_information"
    ]
