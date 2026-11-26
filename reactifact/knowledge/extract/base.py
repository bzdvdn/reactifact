"""Extractor protocol and helpers shared by format modules."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Document, RawDocument


class Extractor(Protocol):
    """Turns a raw document into a canonical document."""

    async def extract(self, document: RawDocument) -> Document:
        """Extract heading-anchored sections from `document`."""
        ...


def document_title(document: RawDocument) -> str:
    """The best title available before format-specific parsing."""
    return str(
        document.metadata.get("title")
        or Path(document.uri).stem
        or document.uri
        or "untitled"
    )


__all__ = ["Extractor", "document_title"]
