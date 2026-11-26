from __future__ import annotations

import asyncio
import importlib.util
import io

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


def _pdf_bytes(text: str) -> bytes:
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_pdf_extractor_reads_page_text() -> None:
    pytest.importorskip("pypdf")
    document = RawDocument(
        step_id="a.pdf",
        uri="a.pdf",
        content_type="application/pdf",
        data=_pdf_bytes("Hello PDF"),
        metadata={"title": "Guide"},
    )
    extracted = asyncio.run(PdfExtractor().extract(document))

    assert extracted.title == "Guide"
    assert [section.heading for section in extracted.sections] == ["Page 1"]
    assert "Hello PDF" in extracted.sections[0].body


def test_docx_extractor_reads_headings_paragraphs_and_tables() -> None:
    docx = pytest.importorskip("docx")
    parsed = docx.Document()
    parsed.add_heading("Guide", level=1)
    parsed.add_paragraph("Install steps")
    parsed.add_heading("Details", level=2)
    parsed.add_paragraph("More text")
    table = parsed.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    buffer = io.BytesIO()
    parsed.save(buffer)

    document = RawDocument(
        step_id="a.docx",
        uri="a.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        data=buffer.getvalue(),
        metadata={"title": "Doc"},
    )
    extracted = asyncio.run(DocxExtractor().extract(document))

    headings = [section.heading for section in extracted.sections]
    assert "Guide" in headings
    assert "Details" in headings
    assert "Table 1" in headings
    guide = next(
        section for section in extracted.sections if section.heading == "Guide"
    )
    assert "Install steps" in guide.body


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
