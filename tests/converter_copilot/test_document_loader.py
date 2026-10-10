import csv

import fitz
import pytest

from converter_copilot.retrieval.document_loader import DocumentLoadError, load_document


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
