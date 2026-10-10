"""Load local PDF, Markdown, and CSV files with stable source locators."""

import csv
import hashlib
import io
from pathlib import Path

import fitz

from converter_copilot.domain.schemas import DocumentChunk


class DocumentLoadError(ValueError):
    """Raised when a source file is unsupported, empty, or malformed."""


def load_document(path: Path, task_id: str) -> list[DocumentChunk]:
    """Extract one or more source-located chunks from a supported local file."""
    path = Path(path)
    if not path.is_file():
        raise DocumentLoadError(f"Document does not exist: {path}")
    extension = path.suffix.lower()
    if extension not in {".pdf", ".md", ".csv"}:
        raise DocumentLoadError(f"Unsupported document type: {extension or '(no extension)'}")
    raw = path.read_bytes()
    if not raw.strip():
        raise DocumentLoadError(f"Document is empty: {path.name}")
    document_id = hashlib.sha256(str(path.resolve()).encode("utf-8") + raw).hexdigest()[:24]

    try:
        if extension == ".pdf":
            parts = _load_pdf(path, raw)
        elif extension == ".md":
            parts = _load_markdown(raw)
        else:
            parts = _load_csv(raw)
    except DocumentLoadError:
        raise
    except (UnicodeDecodeError, csv.Error, fitz.FileDataError, ValueError) as error:
        raise DocumentLoadError(f"Could not parse {path.name}: {error}") from error

    if not parts:
        raise DocumentLoadError(f"No extractable text found in {path.name}")
    return [
        DocumentChunk(
            chunk_id=f"{document_id}-{index:04d}",
            document_id=document_id,
            filename=path.name,
            text=text,
            locator=locator,
            metadata={"task_id": task_id, **metadata},
        )
        for index, (text, locator, metadata) in enumerate(parts, start=1)
    ]


def _load_pdf(path: Path, raw: bytes) -> list[tuple[str, str, dict[str, str]]]:
    document = fitz.open(stream=raw, filetype="pdf")
    try:
        return [
            (text.strip(), f"page {page_number}", {})
            for page_number, page in enumerate(document, start=1)
            if (text := page.get_text("text").strip())
        ]
    finally:
        document.close()


def _load_markdown(raw: bytes) -> list[tuple[str, str, dict[str, str]]]:
    text = raw.decode("utf-8-sig")
    headings: list[tuple[int, str]] = []
    parts: list[tuple[str, str, dict[str, str]]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            body = "\n".join(paragraph).strip()
            if body:
                context = " > ".join(title for _, title in headings)
                parts.append((body, f"heading: {context}" if context else "document body", {"heading": context}))
            paragraph.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            headings = [(depth, heading) for depth, heading in headings if depth < level]
            headings.append((level, title))
        elif stripped:
            paragraph.append(stripped)
        else:
            flush()
    flush()
    return parts


def _load_csv(raw: bytes) -> list[tuple[str, str, dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if not reader.fieldnames:
        raise DocumentLoadError("CSV is missing a header row")
    parts = []
    for row_number, row in enumerate(reader, start=2):
        cleaned = {key: (value or "").strip() for key, value in row.items() if key is not None}
        content = " | ".join(f"{key}: {value}" for key, value in cleaned.items() if value)
        if content:
            parts.append((content, f"row {row_number}", {"row": str(row_number)}))
    return parts
