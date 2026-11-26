"""The generated API reference must match the current docstrings/exports."""

from __future__ import annotations

import pytest
from scripts.gen_reference import OUT, PAGES, render_index, render_page


def test_reference_index_is_current() -> None:
    assert (OUT / "index.md").read_text(encoding="utf-8") == render_index()


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.slug)
def test_reference_page_is_current(page) -> None:
    committed = (OUT / f"{page.slug}.md").read_text(encoding="utf-8")
    assert committed == render_page(page), (
        f"{page.slug}.md is stale; run scripts/gen_reference.py"
    )
