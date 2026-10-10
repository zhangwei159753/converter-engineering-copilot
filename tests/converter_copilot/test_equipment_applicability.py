from converter_copilot.domain.equipment import classify_equipment_applicability
from converter_copilot.agent.nodes import _source_equipment_type
from converter_copilot.domain.schemas import RetrievedChunk


def test_exact_equipment_match_is_direct():
    assert classify_equipment_applicability("CPC-2500 / 690 V", "CPC-2500 / 690 V") == "direct"


def test_same_family_but_different_model_is_related_not_direct():
    assert classify_equipment_applicability("CPC-2500 / 690 V", "CPC-1800 / 690 V") == "related"


def test_different_equipment_family_is_cross_equipment():
    assert classify_equipment_applicability("CPC-2500 / 690 V", "储能双向变流器") == "cross_equipment"


def test_missing_source_equipment_is_unknown():
    assert classify_equipment_applicability("CPC-2500 / 690 V", None) == "unknown"


def test_extract_equipment_type_from_pipe_delimited_csv_chunk():
    chunk = RetrievedChunk(
        chunk_id="chunk-1", document_id="doc-1", filename="fixture.csv",
        text="case_key: X | equipment_type: CPC-2500 / 690 V | symptom: alarm",
        locator="row 2", score=0.9,
    )
    assert _source_equipment_type(chunk) == "CPC-2500 / 690 V"
