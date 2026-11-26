"""Plain text, Markdown and static HTML extractors."""

from __future__ import annotations

import html as html_module
import re

from ..models import Document, RawDocument, Section
from .base import document_title

_HTML_HEADING_RE = re.compile(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


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
        title = first_heading or document_title(document)
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

        title = sections[0].heading if sections else document_title(document)
        return Document(uri=document.uri, title=title, sections=tuple(sections))


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


__all__ = ["HtmlExtractor", "MarkdownExtractor"]
