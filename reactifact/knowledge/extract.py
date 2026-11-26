"""Extraction: `RawDocument` -> Canonical Content Tree (`Document`)."""

from __future__ import annotations

import csv
import html as html_module
import io
import json
import re
from pathlib import Path
from typing import Protocol

from .._extras import require_extra
from .models import Document, RawDocument, Section

_HTML_HEADING_RE = re.compile(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class Extractor(Protocol):
    """Turns a raw document into a canonical document."""

    async def extract(self, document: RawDocument) -> Document:
        """Extract heading-anchored sections from `document`."""
        ...


class MarkdownExtractor:
    """Splits plain text or Markdown on heading lines (`#`..`######`)."""

    async def extract(self, document: RawDocument) -> Document:
        """Split a text document into heading-anchored sections."""
        text = document.data.decode("utf-8", errors="replace")
        sections: list[Section] = []
        current_heading = ""
        current_body: list[str] = []
        first_heading = ""

        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            level = _heading_level(stripped)
            if level is not None:
                if current_heading or current_body:
                    sections.append(
                        Section(
                            heading=current_heading,
                            body="\n".join(current_body).strip(),
                        )
                    )
                if not first_heading:
                    first_heading = stripped[level:].strip()
                current_heading = stripped[level:].strip()
                current_body = []
            else:
                current_body.append(raw_line)

        sections.append(
            Section(heading=current_heading, body="\n".join(current_body).strip())
        )
        title = first_heading or str(
            document.metadata.get("title") or Path(document.uri).stem or "untitled"
        )
        return Document(uri=document.uri, title=title, sections=tuple(sections))


class HtmlExtractor:
    """Extracts heading/body pairs from a static HTML document."""

    async def extract(self, document: RawDocument) -> Document:
        """Split an HTML document on its heading tags."""
        text = document.data.decode("utf-8", errors="replace")
        parts = _HTML_HEADING_RE.split(text)
        sections: list[Section] = []

        lead = _clean(parts[0])
        if lead:
            sections.append(Section(heading="", body=lead))

        for index in range(1, len(parts), 3):
            heading = _strip_tags(parts[index + 1]).strip()
            body = _clean(parts[index + 2])
            sections.append(Section(heading=heading, body=body))

        title = (
            sections[0].heading if sections else str(document.metadata.get("title", ""))
        )
        return Document(uri=document.uri, title=title, sections=tuple(sections))


class CsvExtractor:
    """Turns CSV rows into Markdown table sections.

    Rows are grouped so a large export does not become one unwieldy section;
    headings carry the row range, which doubles as a citation coordinate.
    """

    def __init__(self, rows_per_section: int = 100) -> None:
        self.rows_per_section = max(1, rows_per_section)

    async def extract(self, document: RawDocument) -> Document:
        """Render a CSV document as `(rows N-M)` table sections."""
        text = document.data.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        title = _title(document)
        if not rows:
            return Document(uri=document.uri, title=title)
        header = rows[0]
        width = len(header)
        sections: list[Section] = []
        for start in range(1, len(rows), self.rows_per_section):
            block = rows[start : start + self.rows_per_section]
            end = start + len(block) - 1
            lines = [
                "| " + " | ".join(header) + " |",
                "| " + " | ".join("---" for _ in header) + " |",
            ]
            for row in block:
                padded = list(row[:width]) + [""] * max(0, width - len(row))
                lines.append("| " + " | ".join(padded) + " |")
            sections.append(
                Section(
                    heading=f"{title} (rows {start}-{end})",
                    body="\n".join(lines),
                )
            )
        return Document(uri=document.uri, title=title, sections=tuple(sections))


class JsonExtractor:
    """Turns JSON into heading/body sections without flattening the structure."""

    async def extract(self, document: RawDocument) -> Document:
        """Render object keys or array items as sections."""
        value = json.loads(document.data.decode("utf-8", errors="replace"))
        title = _title(document)
        return Document(
            uri=document.uri,
            title=title,
            sections=tuple(_json_sections(value, title)),
        )


class PdfExtractor:
    """Extracts one section per PDF page (requires the ``knowledge-pdf`` extra)."""

    async def extract(self, document: RawDocument) -> Document:
        """Render pages as `Page N` sections."""
        pypdf = require_extra("PdfExtractor", "pypdf", "knowledge-pdf")
        reader = pypdf.PdfReader(io.BytesIO(document.data))
        sections = [
            Section(
                heading=f"Page {index}",
                body=(page.extract_text() or "").strip(),
            )
            for index, page in enumerate(reader.pages, start=1)
        ]
        title = _title(document)
        metadata = getattr(reader, "metadata", None)
        declared = getattr(metadata, "title", None) if metadata is not None else None
        if declared:
            title = str(declared)
        return Document(uri=document.uri, title=title, sections=tuple(sections))


class DocxExtractor:
    """Extracts DOCX headings, paragraphs and tables (``knowledge-office``)."""

    async def extract(self, document: RawDocument) -> Document:
        """Render DOCX headings/paragraphs as sections and tables as appendices."""
        docx = require_extra("DocxExtractor", "docx", "knowledge-office")
        parsed = docx.Document(io.BytesIO(document.data))
        title = _title(document)
        sections: list[Section] = []
        heading = ""
        body: list[str] = []

        def flush() -> None:
            nonlocal heading, body
            if heading or body:
                sections.append(Section(heading=heading, body="\n".join(body).strip()))
            body = []

        for paragraph in parsed.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = getattr(getattr(paragraph, "style", None), "name", "") or ""
            if style.startswith("Heading") or style == "Title":
                flush()
                heading = text
                if not title or title == Path(document.uri).stem:
                    title = text
            else:
                body.append(text)
        flush()

        for index, table in enumerate(parsed.tables, start=1):
            rows = [
                " | ".join(cell.text.strip() for cell in row.cells)
                for row in table.rows
            ]
            if rows:
                sections.append(Section(heading=f"Table {index}", body="\n".join(rows)))
        return Document(uri=document.uri, title=title, sections=tuple(sections))


_MARKDOWN_TYPES = {"text/markdown", "text/plain", "text/x-markdown"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_CSV_TYPES = {"text/csv", "application/csv"}
_JSON_TYPES = {"application/json", "text/json"}
_PDF_TYPES = {"application/pdf"}
_DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}


class AutoExtractor:
    """Dispatches to a format extractor by content type, then by extension."""

    async def extract(self, document: RawDocument) -> Document:
        """Extract using the declared content type or the URI suffix."""
        content_type = document.content_type.split(";", 1)[0].strip().lower()
        suffix = Path(document.uri).suffix.lower()

        if content_type in _MARKDOWN_TYPES or (
            not content_type and suffix in {".md", ".markdown", ".txt"}
        ):
            return await MarkdownExtractor().extract(document)
        if content_type in _HTML_TYPES or suffix in {".html", ".htm"}:
            return await HtmlExtractor().extract(document)
        if content_type in _CSV_TYPES or suffix == ".csv":
            return await CsvExtractor().extract(document)
        if (
            content_type in _JSON_TYPES
            or content_type.endswith("+json")
            or (suffix == ".json")
        ):
            return await JsonExtractor().extract(document)
        if content_type in _PDF_TYPES or suffix == ".pdf":
            return await PdfExtractor().extract(document)
        if content_type in _DOCX_TYPES or suffix == ".docx":
            return await DocxExtractor().extract(document)
        raise ValueError(
            f"unsupported content type {document.content_type!r} for "
            f"{document.uri!r}; install an extractor extra or pass extractor="
        )


def _json_sections(value: object, title: str) -> list[Section]:
    if isinstance(value, dict):
        return [
            Section(heading=str(key), body=_json_body(item))
            for key, item in value.items()
        ]
    if isinstance(value, list):
        sections: list[Section] = []
        for index, item in enumerate(value):
            heading = ""
            if isinstance(item, dict):
                heading = str(
                    item.get("title") or item.get("name") or item.get("id") or ""
                )
            sections.append(
                Section(
                    heading=heading or f"{title}[{index}]",
                    body=_json_body(item),
                )
            )
        return sections
    return [Section(heading=title, body=_json_body(value))]


def _json_body(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _title(document: RawDocument) -> str:
    return str(
        document.metadata.get("title")
        or Path(document.uri).stem
        or document.uri
        or "untitled"
    )


def _strip_tags(text: str) -> str:
    return html_module.unescape(_HTML_TAG_RE.sub(" ", text))


def _clean(text: str) -> str:
    return " ".join(_strip_tags(text).split())


def _heading_level(line: str) -> int | None:
    hashes = 0
    for char in line:
        if char == "#":
            hashes += 1
        else:
            break
    if 1 <= hashes <= 6 and len(line) > hashes and line[hashes] == " ":
        return hashes
    return None


__all__ = [
    "AutoExtractor",
    "CsvExtractor",
    "DocxExtractor",
    "Extractor",
    "HtmlExtractor",
    "JsonExtractor",
    "MarkdownExtractor",
    "PdfExtractor",
]
