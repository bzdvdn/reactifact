"""Content-type dispatch across the built-in extractors."""

from __future__ import annotations

from pathlib import Path

from ..models import Document, RawDocument
from .docx import DocxExtractor
from .pdf import PdfExtractor
from .structured import CsvExtractor, JsonExtractor
from .text import HtmlExtractor, MarkdownExtractor

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


__all__ = ["AutoExtractor"]
