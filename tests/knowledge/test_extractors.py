from __future__ import annotations

import asyncio
import importlib.util

import pytest
from reactifact.knowledge import (
    AutoExtractor,
    CsvExtractor,
    DocxExtractor,
    JsonExtractor,
    PdfExtractor,
    RawDocument,
)


def _document(text: str, *, uri: str, content_type: str) -> RawDocument:
    return RawDocument.from_text(
        step_id=uri,
        uri=uri,
        text=text,
        content_type=content_type,
    )


def test_csv_extractor_builds_markdown_table_sections() -> None:
    document = _document(
        "name,qty\napple,2\npear,3\nplum,4\n",
        uri="stock.csv",
        content_type="text/csv",
    )
    extracted = asyncio.run(CsvExtractor(rows_per_section=2).extract(document))

    assert extracted.title == "stock"
    assert [section.heading for section in extracted.sections] == [
        "stock (rows 1-2)",
        "stock (rows 3-3)",
    ]
    assert "| name | qty |" in extracted.sections[0].body
    assert "apple" in extracted.sections[0].body
    assert "plum" in extracted.sections[1].body


def test_json_extractor_keeps_keys_and_items_as_sections() -> None:
    object_doc = _document(
        '{"title": "Guide", "steps": ["install", "run"]}',
        uri="guide.json",
        content_type="application/json",
    )
    extracted = asyncio.run(JsonExtractor().extract(object_doc))
    assert [section.heading for section in extracted.sections] == [
        "title",
        "steps",
    ]
    assert "install" in extracted.sections[1].body

    list_doc = _document(
        '[{"name": "alpha"}, {"name": "beta"}]',
        uri="items.json",
        content_type="application/json",
    )
    listed = asyncio.run(JsonExtractor().extract(list_doc))
    assert [section.heading for section in listed.sections] == ["alpha", "beta"]


def test_auto_extractor_falls_back_to_the_uri_suffix() -> None:
    document = _document(
        "a,b\n1,2\n",
        uri="data.csv",
        content_type="application/octet-stream",
    )
    extracted = asyncio.run(AutoExtractor().extract(document))
    assert extracted.title == "data"
    assert "| a | b |" in extracted.sections[0].body


def test_auto_extractor_rejects_unknown_formats() -> None:
    document = _document(
        "binary",
        uri="archive.bin",
        content_type="application/octet-stream",
    )
    with pytest.raises(ValueError, match="unsupported content type"):
        asyncio.run(AutoExtractor().extract(document))


def test_optional_extractors_name_their_extra() -> None:
    if importlib.util.find_spec("pypdf") is None:
        with pytest.raises(ImportError, match="knowledge-pdf"):
            asyncio.run(
                PdfExtractor().extract(
                    _document("x", uri="a.pdf", content_type="application/pdf")
                )
            )
    if importlib.util.find_spec("docx") is None:
        with pytest.raises(ImportError, match="knowledge-office"):
            asyncio.run(
                DocxExtractor().extract(
                    _document(
                        "x", uri="a.docx", content_type="application/octet-stream"
                    )
                )
            )
