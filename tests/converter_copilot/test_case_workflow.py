import asyncio

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.domain.schemas import (
    CaseRecord,
    RetrievedChunk,
    TaskMode,
)
from converter_copilot.retrieval.case_extractor import extract_case_records


class FakeStructuredModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return self.result


class FakeSearch:
    def search(self, query, *, limit=5):
        return []


class FakeVector:
    def __init__(self, chunks):
        self.chunks = chunks

    def search(self, query, *, limit=5, filters=None):
        return self.chunks[:limit]


class FakeCaseStore:
    def __init__(self, cases):
        self.cases = cases

    def list_cases(self):
        return self.cases


class UnusedModel:
    pass


def _chunk(chunk_id="ev-case-1", score=0.91, text="PV inverter DC bus overvoltage at 50% load"):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-case-1",
        filename="maintenance-cases.csv",
        text=text,
        locator="row 2",
        score=score,
    )


def _case(**updates):
    values = {
        "case_id": "case-1",
        "equipment_type": "PV inverter",
        "operating_conditions": {"load": "50%"},
        "symptom": "DC bus overvoltage",
        "cause": "Braking unit fault",
        "validation_result": "Resistance out of range",
        "handling_result": "Replaced braking resistor",
        "evidence_ids": ["ev-case-1"],
    }
    values.update(updates)
    return CaseRecord(**values)


def _state(conditions=None):
    return {
        "task_id": "case-task-1",
        "inputs": {
            "symptom": "DC bus overvoltage",
            "equipment_type": "PV inverter",
            "operating_conditions": conditions if conditions is not None else {"load": "50%"},
        },
    }


def _graph(case, chunk=None):
    deps = WorkflowDeps(
        model=UnusedModel(),
        search=FakeSearch(),
        vector_index=FakeVector([chunk or _chunk()]),
        case_store=FakeCaseStore([case]),
    )
    return build_workflow(TaskMode.CASE_RETRIEVAL, deps)


def test_matching_equipment_and_symptom_returns_cited_case():
    state = asyncio.run(_graph(_case()).ainvoke(_state()))
    match = state["report"].result.matches[0]
    assert "equipment_type" in match.matching_fields
    assert "symptom" in match.matching_fields
    assert match.evidence_ids == ["ev-case-1"]
    assert state["report"].evidence[0].locator == "row 2"
    assert "not establish causality" in state["report"].quality_warnings[0].lower()


def test_meaningful_operating_condition_differences_are_explained():
    state = asyncio.run(_graph(_case(operating_conditions={"load": "25%", "ambient": "20 C"})).ainvoke(_state()))
    match = state["report"].result.matches[0]
    assert any("load" in difference and "50%" in difference and "25%" in difference for difference in match.differing_fields)
    assert "equipment_type" in match.matching_fields


def test_low_similarity_candidate_is_rejected():
    state = asyncio.run(_graph(_case(), chunk=_chunk(score=0.05, text="unrelated pump bearing noise")).ainvoke(_state()))
    assert state["report"].result.matches == []
    assert state["report"].completion_status == "partial"


def test_case_with_missing_fields_is_retained_with_applicability_caveats():
    state = asyncio.run(_graph(_case(equipment_type=None, operating_conditions={}, cause=None, validation_result=None)).ainvoke(_state()))
    match = state["report"].result.matches[0]
    assert any("equipment type" in item.lower() for item in match.applicability_limitations)
    assert any("cause" in item.lower() or "validation" in item.lower() for item in match.applicability_limitations)
    assert state["report"].completion_status == "partial"


def test_case_extractor_preserves_source_evidence_and_tolerates_missing_outcomes():
    model = FakeStructuredModel({
        "cases": [{
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "cause": None,
            "validation_result": None,
            "handling_result": None,
            "operating_conditions": {},
        }]
    })
    cases = asyncio.run(extract_case_records(
        "Fault log: PV inverter reported DC bus overvoltage.",
        evidence_id="ev-source-1",
        model=model,
    ))
    assert len(cases) == 1
    assert cases[0].evidence_ids == ["ev-source-1"]
    assert cases[0].cause is None
