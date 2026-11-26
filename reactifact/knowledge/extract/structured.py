"""Dependency-free structured extractors: CSV and JSON."""

from __future__ import annotations

import csv
import io
import json

from ..models import Document, RawDocument, Section
from .base import document_title


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
        title = document_title(document)
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
        title = document_title(document)
        return Document(
            uri=document.uri,
            title=title,
            sections=tuple(_json_sections(value, title)),
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


__all__ = ["CsvExtractor", "JsonExtractor"]
