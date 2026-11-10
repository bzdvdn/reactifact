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
    end = html.index("function producesBlock", start)
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


def test_work_ui_has_contract_flow_and_chip_filters():
    html = _ui_run_html()
    assert "contract-flow" in html
    assert "data-contract-agent" in html
    assert "data-filter-kind" in html
    assert "Consumes / inputs" in html
    assert "Outputs / writes" in html
    assert "tl-row child" in html
    assert "function tlRow" in html
    assert "function tlStart" in html
    assert "step · " in html
    assert "llm · " in html
    assert "Causal chain" in html
    assert "renderCausal" in html
    assert "Mermaid source (export)" in html
    assert "data-cx-span" in html
    assert "data-span" in html
    assert "focusSpan" in html
