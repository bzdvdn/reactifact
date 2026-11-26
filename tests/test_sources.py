import asyncio

from reactifact.sources import FileSystemSource, SourceRef


def test_filesystem_source_resolve(tmp_path):
    file = tmp_path / "test.md"
    file.write_text("Hello")
    src = FileSystemSource(str(tmp_path))
    ref = SourceRef(source_id="filesystem", locator="test.md")
    content = asyncio.run(src.resolve(ref))
    assert content == "Hello"


def test_source_ref_stable_id():
    ref1 = SourceRef(source_id="gitlab", locator="repo/file.md")
    ref2 = SourceRef(source_id="gitlab", locator="repo/file.md")
    assert ref1.stable_id() == ref2.stable_id()


def test_source_ref_fragment_distinguishes_chunks():
    doc = SourceRef(source_id="kb", locator="a.md")
    one = SourceRef(source_id="kb", locator="a.md", fragment="chunk-1")
    two = SourceRef(source_id="kb", locator="a.md", fragment="chunk-2")

    assert one.stable_id() != two.stable_id()
    assert one.stable_id() != doc.stable_id()
