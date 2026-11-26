"""PDF extractor (optional ``knowledge-pdf`` extra)."""

from __future__ import annotations

import io

from ..._extras import require_extra
from ..models import Document, RawDocument, Section
from .base import document_title


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
        title = document_title(document)
        metadata = getattr(reader, "metadata", None)
        declared = getattr(metadata, "title", None) if metadata is not None else None
        if declared:
            title = str(declared)
        return Document(uri=document.uri, title=title, sections=tuple(sections))


__all__ = ["PdfExtractor"]
