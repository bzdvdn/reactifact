"""Trace UI template sanity checks (no browser in CI)."""

from __future__ import annotations

from pathlib import Path

import reactifact


def _ui_run_html() -> str:
    root = Path(reactifact.__file__).parent
    return (root / "tracing" / "templates" / "ui_run.html").read_text()


def test_span_renderer_defines_the_tool_count_it_uses():
    """Regression: `spanHtml` referenced `toolCount` without declaring it, which
    threw a ReferenceError and left the whole Work section empty."""
    html = _ui_run_html()
    start = html.index("function spanHtml(s){")
    end = html.index("function flowHtml", start)
    body = html[start:end]

    assert "const toolCount" in body
    assert body.index("const toolCount") < body.index("toolCount?")


def test_artifact_preview_opens_the_full_data_modal():
    html = _ui_run_html()
    assert "adata-preview" in html
    assert "data-art-key" in html
    assert "openDataModal" in html
    assert 'id="data-modal"' in html
    assert "const ARTIFACTS={}" in html
