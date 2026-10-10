import csv

import fitz
import pytest

from converter_copilot.config import ImportLimits
from converter_copilot.retrieval.document_loader import DocumentLoadError, load_document
from converter_copilot.retrieval.import_validation import (
    ImportValidationError,
    retain_source,
    validate_upload,
)


def test_pdf_chunks_keep_page_locator(tmp_path):
    path = tmp_path / "manual.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "DC bus overvoltage: inspect braking unit.")
    pdf.save(path)
    pdf.close()

    chunks = load_document(path, task_id="task-1")
    assert len(chunks) == 1
    assert chunks[0].filename == "manual.pdf"
    assert chunks[0].locator == "page 1"
    assert "braking unit" in chunks[0].text


def test_markdown_chunks_keep_heading_context(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# 直流侧故障\n\n检查母线电容。\n\n## 处理步骤\n\n测量纹波电压。\n", encoding="utf-8")
    chunks = load_document(path, task_id="task-1")
    assert len(chunks) == 2
    assert chunks[0].metadata["heading"] == "直流侧故障"
    assert chunks[1].metadata["heading"] == "直流侧故障 > 处理步骤"


def test_csv_chunks_keep_row_locator(tmp_path):
    path = tmp_path / "alarms.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["alarm", "cause"])
        writer.writeheader()
        writer.writerow({"alarm": "F021", "cause": "直流过压"})
    chunks = load_document(path, task_id="task-1")
    assert len(chunks) == 1
    assert chunks[0].locator == "row 2"
    assert "F021" in chunks[0].text


@pytest.mark.parametrize("filename, content", [("manual.docx", b"x"), ("empty.md", b""), ("bad.csv", b'"unterminated')])
def test_unsupported_empty_or_malformed_documents_raise_typed_error(tmp_path, filename, content):
    path = tmp_path / filename
    path.write_bytes(content)
    with pytest.raises(DocumentLoadError):
        load_document(path, task_id="task-1")


def test_import_limits_load_from_environment_and_reject_non_positive_values(monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_BYTES", "1234")
    monkeypatch.setenv("IMPORT_MAX_PDF_PAGES", "7")
    monkeypatch.setenv("IMPORT_MAX_CSV_ROWS", "8")
    monkeypatch.setenv("IMPORT_MAX_CHUNKS", "9")

    assert ImportLimits.from_env() == ImportLimits(max_bytes=1234, max_pdf_pages=7, max_csv_rows=8, max_chunks=9)
    with pytest.raises(ValueError, match="must be positive"):
        ImportLimits(max_chunks=0)


def test_invalid_utf8_upload_returns_safe_validation_error():
    with pytest.raises(ImportValidationError) as error:
        validate_upload("notes.md", b"\xff\xfe private document text", ImportLimits())

    assert error.value.detail["code"] == "invalid_utf8"
    assert "private document text" not in str(error.value)


def test_import_and_chunk_ids_are_stable_for_content_and_parser_versions(tmp_path):
    payload = b"# Manual\n\nCheck the DC bus voltage.\n"
    first = validate_upload("manual.md", payload, ImportLimits())
    second = validate_upload("renamed.md", payload, ImportLimits())
    changed_parser = validate_upload("manual.md", payload, ImportLimits(), parser_version="markdown-v2")

    assert first.import_id == second.import_id
    assert changed_parser.import_id != first.import_id
    assert first.chunks[0].document_id == first.import_id
    assert first.chunks[0].chunk_id == second.chunks[0].chunk_id


def test_retained_source_survives_validation_temp_directory(tmp_path):
    source = validate_upload("notes.md", b"# Manual\n\nOriginal bytes.\n", ImportLimits())
    destination = retain_source(source, tmp_path / "sources")

    assert destination.read_bytes() == source.payload
    assert destination.parent == tmp_path / "sources"
