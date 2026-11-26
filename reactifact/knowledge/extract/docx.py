"""DOCX extractor (optional ``knowledge-office`` extra)."""

from __future__ import annotations

import io
from pathlib import Path

from ..._extras import require_extra
from ..models import Document, RawDocument, Section
from .base import document_title


class DocxExtractor:
    """Extracts DOCX headings, paragraphs and tables (``knowledge-office``)."""

    async def extract(self, document: RawDocument) -> Document:
        """Render DOCX headings/paragraphs as sections and tables as appendices."""
        docx = require_extra("DocxExtractor", "docx", "knowledge-office")
        parsed = docx.Document(io.BytesIO(document.data))
        title = document_title(document)
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


__all__ = ["DocxExtractor"]
