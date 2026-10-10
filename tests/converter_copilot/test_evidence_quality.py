from datetime import datetime, timezone

from converter_copilot.domain.evidence import validate_report_evidence
from converter_copilot.domain.schemas import ConverterReport


def _report(claims, evidence):
    return ConverterReport(
        task_id="task-1",
        mode="fault_analysis",
        result={"hypotheses": [], "checks": []},
        claims=claims,
        evidence=evidence,
        quality_warnings=[],
        completion_status="completed",
    )


def _claim(*evidence_ids, claim_id="claim-1"):
    return {"claim_id": claim_id, "text": "直流母线电压异常", "claim_type": "fact", "evidence_ids": list(evidence_ids)}


def _evidence(evidence_id="ev-1", source_status="available"):
    return {
        "evidence_id": evidence_id,
        "task_id": "task-1",
        "title": "设备维护手册",
        "source_type": "document",
        "excerpt": "检查直流母线电压。",
        "accessed_at": datetime.now(timezone.utc),
        "source_status": source_status,
    }


def test_valid_citation_supports_claim():
    result = validate_report_evidence(_report([_claim("ev-1")], [_evidence()]))
    assert result.supported_claim_ids == ["claim-1"]
    assert result.conflicting_claim_ids == []
    assert result.unverified_claim_ids == []
    assert result.warnings == []


def test_missing_evidence_id_leaves_claim_unverified():
    result = validate_report_evidence(_report([_claim("missing")], [_evidence()]))
    assert result.unverified_claim_ids == ["claim-1"]
    assert any("missing" in warning for warning in result.warnings)


def test_conflicting_source_marks_claim_conflicting():
    result = validate_report_evidence(_report([_claim("ev-1")], [_evidence(source_status="conflicting")]))
    assert result.conflicting_claim_ids == ["claim-1"]


def test_duplicate_evidence_ids_are_warned_and_not_trusted():
    result = validate_report_evidence(_report([_claim("ev-1")], [_evidence(), _evidence()]))
    assert result.unverified_claim_ids == ["claim-1"]
    assert any("duplicate" in warning.lower() for warning in result.warnings)


def test_claim_without_citations_is_unverified():
    result = validate_report_evidence(_report([_claim()], []))
    assert result.unverified_claim_ids == ["claim-1"]
    assert any("no evidence" in warning.lower() for warning in result.warnings)
