import json

import pytest
from pydantic import ValidationError

from converter_copilot.domain.schemas import (
    CaseRetrievalInput,
    ConverterReport,
    Evidence,
    FaultAnalysisInput,
    TaskCreate,
    TaskMode,
    TechnicalResearchInput,
)


@pytest.mark.parametrize(
    ("mode", "inputs"),
    [
        (TaskMode.FAULT_ANALYSIS, {"equipment_type": "光伏逆变器", "symptom": "直流母线电压波动"}),
        (TaskMode.TECHNICAL_RESEARCH, {"question": "比较两电平与三电平拓扑"}),
        (TaskMode.CASE_RETRIEVAL, {"symptom": "启动后过流"}),
    ],
)
def test_task_create_accepts_input_for_each_mode(mode, inputs):
    request = TaskCreate(mode=mode, inputs=inputs)
    assert request.mode == mode
    assert request.inputs.model_dump()


def test_task_create_rejects_mismatched_mode_input():
    with pytest.raises(ValidationError):
        TaskCreate(mode="fault_analysis", inputs={"question": "不匹配"})


def test_task_create_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        TaskCreate(mode="unknown", inputs={"symptom": "过热"})


@pytest.mark.parametrize("model, payload", [(FaultAnalysisInput, {}), (TechnicalResearchInput, {}), (CaseRetrievalInput, {})])
def test_mode_input_requires_its_main_prompt_field(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_fault_context_defaults_are_independent():
    first = FaultAnalysisInput(equipment_type="逆变器", symptom="过流")
    second = FaultAnalysisInput(equipment_type="逆变器", symptom="过流")
    first.operating_conditions["dc_voltage"] = 700
    first.attachment_ids.append("file-1")
    assert second.operating_conditions == {}
    assert second.attachment_ids == []


def test_report_and_evidence_json_round_trip():
    evidence = Evidence(
        evidence_id="ev-1",
        task_id="task-1",
        title="示例手册",
        source_type="document",
        excerpt="母线过压可能与制动单元异常有关。",
        accessed_at="2026-10-10T10:00:00Z",
        related_claim_ids=["claim-1"],
        source_status="available",
    )
    report = ConverterReport(
        task_id="task-1",
        mode="fault_analysis",
        result={"hypotheses": [], "checks": ["测量直流母线纹波"]},
        claims=[{"claim_id": "claim-1", "text": "需检查制动单元", "claim_type": "recommendation"}],
        evidence=[evidence],
        quality_warnings=[],
        completion_status="completed",
    )
    serialized = report.model_dump_json()
    restored = ConverterReport.model_validate_json(json.dumps(json.loads(serialized)))
    assert restored == report
