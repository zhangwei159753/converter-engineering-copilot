"""Validate bounded source uploads before retrieval or persistence side effects."""

import csv
import hashlib
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz

from converter_copilot.config import ImportLimits
from converter_copilot.domain.schemas import DocumentChunk
from converter_copilot.retrieval.document_loader import DocumentLoadError, load_document


@dataclass(frozen=True)
class ValidatedImport:
    """Fully parsed upload data safe to pass to the import lifecycle."""

    filename: str
    file_type: str
    content_sha256: str
    parser_version: str
    chunker_version: str
    chunks: list[DocumentChunk]
    case_rows: list[dict[str, str]]
    payload: bytes


class ImportValidationError(ValueError):
    """Stable, safe validation failure suitable for an API response."""

    def __init__(self, code: str, message: str, filename: str, *, row: int | None = None, status_code: int = 400):
        """Build a safe structured error without retaining uploaded text."""
        super().__init__(message)
        self.status_code = status_code
        self.detail = {"code": code, "message": message, "filename": filename, "row": row}


def validate_upload(filename: str, payload: bytes, limits: ImportLimits) -> ValidatedImport:
    """Parse the complete bounded payload and enforce format-specific ceilings."""
    safe_name = Path(filename.replace("\\", "/")).name or "upload"
    suffix = Path(safe_name).suffix.lower()
    file_type = suffix.removeprefix(".")
    if suffix not in {".pdf", ".md", ".csv"}:
        raise ImportValidationError(
            "unsupported_file_type", "Supported formats are PDF, Markdown, and CSV.", safe_name, status_code=415
        )
    if len(payload) > limits.max_bytes:
        raise ImportValidationError(
            "file_too_large", f"File exceeds the {limits.max_bytes}-byte upload limit.", safe_name, status_code=413
        )
    if not payload.strip():
        raise ImportValidationError("empty_file", "The uploaded file is empty.", safe_name)

    case_rows: list[dict[str, str]] = []
    if suffix == ".csv":
        case_rows = _validate_csv(safe_name, payload, limits)
    elif suffix == ".md":
        try:
            text = payload.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise ImportValidationError("invalid_utf8", "Markdown must use valid UTF-8 encoding.", safe_name) from error
        if not text.strip():
            raise ImportValidationError("empty_document", "No extractable text was found.", safe_name)
    else:
        try:
            document = fitz.open(stream=payload, filetype="pdf")
        except (fitz.FileDataError, ValueError) as error:
            raise ImportValidationError("invalid_pdf", "PDF could not be opened.", safe_name) from error
        try:
            if document.page_count > limits.max_pdf_pages:
                raise ImportValidationError(
                    "pdf_page_limit_exceeded",
                    f"PDF exceeds the {limits.max_pdf_pages}-page limit.",
                    safe_name,
                    status_code=413,
                )
        finally:
            document.close()

    try:
        with tempfile.TemporaryDirectory(prefix="converter-validate-") as directory:
            path = Path(directory) / safe_name
            path.write_bytes(payload)
            chunks = load_document(path, task_id="case-import")
    except DocumentLoadError as error:
        # The loader may include file content or filesystem paths in diagnostics.
        code = "invalid_csv" if suffix == ".csv" else "invalid_document"
        raise ImportValidationError(code, "The uploaded file could not be parsed into source chunks.", safe_name) from error
    if suffix == ".csv" and not case_rows:
        raise ImportValidationError("empty_csv", "CSV must contain at least one valid case row.", safe_name)
    if len(chunks) > limits.max_chunks:
        raise ImportValidationError(
            "chunk_limit_exceeded",
            f"File produces more than the {limits.max_chunks}-chunk limit.",
            safe_name,
            status_code=413,
        )

    version = {"pdf": "pdf-v1", "md": "markdown-v1", "csv": "csv-v1"}[file_type]
    return ValidatedImport(
        filename=safe_name,
        file_type=file_type,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        parser_version=version,
        chunker_version="locator-chunk-v1",
        chunks=chunks,
        case_rows=case_rows,
        payload=payload,
    )


def _validate_csv(filename: str, payload: bytes, limits: ImportLimits) -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8-sig", errors="strict")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers or any(not header.strip() for header in headers):
            raise ImportValidationError("missing_csv_headers", "CSV must contain non-empty headers.", filename)
        normalized = [header.strip() for header in headers]
        if len(set(normalized)) != len(normalized):
            raise ImportValidationError("duplicate_csv_headers", "CSV contains duplicate headers.", filename)
        if "symptom" not in normalized:
            raise ImportValidationError(
                "missing_required_field", "CSV is missing the required symptom header.", filename
            )
        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if row_number - 1 > limits.max_csv_rows:
                raise ImportValidationError(
                    "csv_row_limit_exceeded",
                    f"CSV exceeds the {limits.max_csv_rows}-row limit.",
                    filename,
                    row=row_number,
                    status_code=413,
                )
            if None in row or any(row.get(header) is None for header in headers):
                raise ImportValidationError("invalid_csv_row", f"CSV row {row_number} has an invalid number of columns.", filename, row=row_number)
            cleaned = {header.strip(): (row.get(header) or "").strip() for header in headers}
            if not cleaned.get("symptom"):
                raise ImportValidationError(
                    "missing_required_field",
                    f"CSV row {row_number} is missing the required symptom field.",
                    filename,
                    row=row_number,
                    status_code=422,
                )
            rows.append(cleaned)
    except UnicodeDecodeError as error:
        raise ImportValidationError("invalid_utf8", "CSV must use valid UTF-8 encoding.", filename) from error
    except csv.Error as error:
        raise ImportValidationError("invalid_csv", "CSV contains malformed quoting or rows.", filename) from error
    return rows
