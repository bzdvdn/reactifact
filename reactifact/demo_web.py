"""`reactifact demo --web` — the same auditable answer, in a browser.

No API key and no project files: the demo pipeline runs once with an in-memory
trace store, then a small FastAPI app serves the answer, its provenance graph,
the static contracts page and the trace dashboard.
"""

from __future__ import annotations

import asyncio
import html
import threading
import webbrowser
from typing import Any
from urllib.parse import quote

from .audit import context_hash
from .contracts.report import describe_contracts
from .contracts.web import create_contracts_router
from .demo import QUESTION, STEPS, Answer, Spend, Variance, run_demo
from .step.group import normalize_units
from .tracing import Tracer, TraceStore
from .tracing.web import create_trace_router
from .viz import context_to_mermaid
from .web.common import install_error_handlers, install_request_id


def _page(
    *,
    answer: Answer,
    spend: Spend | None,
    variance: Variance | None,
    hash_first: str,
    hash_second: str,
    trace_id: str,
    mermaid: str,
) -> str:
    figures = ""
    if spend is not None:
        months = " · ".join(
            f"{month} ${value:,.0f}" for month, value in spend.by_month.items()
        )
        figures += (
            f"<div class='fact'><span>cloud spend</span>"
            f"<strong>${spend.total:,.0f}</strong><small>{html.escape(months)}</small></div>"
        )
    if variance is not None:
        verdict = "approval required" if not variance.within_policy else "within"
        figures += (
            f"<div class='fact'><span>policy</span>"
            f"<strong>{variance.threshold:.0%} → {verdict}</strong>"
            f"<small>${variance.actual:,.0f} vs ${variance.budget:,.0f}</small></div>"
        )
    citations = "".join(
        f"<li><code>{html.escape(citation)}</code></li>"
        for citation in answer.citations
    )
    repeat = "identical" if hash_first == hash_second else "DIFFERENT"
    repeat_class = "ok" if hash_first == hash_second else "bad"
    trace_link = (
        f"<a class='button' href='/traces/{quote(trace_id, safe='')}'>Open trace</a>"
        if trace_id
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reactifact demo</title>
<style>
:root {{ color-scheme: light dark; --bg:#f7f7fb; --card:#fff; --text:#17171f; --muted:#666b7a;
  --line:#e3e4ec; --accent:#4f46e5; --accent-soft:#eef0ff; --ok:#15803d; --bad:#b91c1c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111218; --card:#181a22; --text:#ececf4;
  --muted:#9a9db0; --line:#2a2c38; --accent:#8b93ff; --accent-soft:#222448; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 -apple-system,
  BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:40px 20px 72px; }}
header {{ display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; margin-bottom:28px; }}
h1 {{ font-size:28px; margin:0; letter-spacing:-.02em; }}
h1 span {{ color:var(--accent); }}
.badge {{ border:1px solid var(--line); border-radius:999px; padding:3px 9px; font-size:12px;
  color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; }}
.card h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
  margin:0 0 12px; }}
.answer {{ font-size:19px; line-height:1.6; margin:0; }}
.fact {{ border-top:1px solid var(--line); padding-top:12px; margin-top:12px; display:flex;
  flex-direction:column; gap:2px; }}
.fact span {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
.fact strong {{ font-size:18px; }}
.fact small {{ color:var(--muted); }}
ul {{ margin:0; padding-left:18px; }}
li + li {{ margin-top:4px; }}
code {{ font-size:13px; }}
.hash {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px;
  word-break:break-all; color:var(--muted); }}
.ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }}
.actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
.button {{ display:inline-block; border:1px solid var(--line); border-radius:9px; padding:7px 12px;
  color:var(--text); text-decoration:none; background:var(--card); }}
.button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
pre.mermaid {{ margin:0; white-space:pre-wrap; }}
details {{ margin-top:12px; }}
summary {{ cursor:pointer; color:var(--muted); }}
</style>
</head>
<body>
<main>
<header>
  <h1>reactifact <span>demo</span></h1>
  <span class="badge">computed · cited · reproducible</span>
  <span class="badge">no API key</span>
</header>
<section class="grid">
  <div class="card" style="grid-column:1/-1">
    <h2>Question</h2>
    <p>{html.escape(QUESTION)}</p>
    <h2 style="margin-top:22px">Answer</h2>
    <p class="answer">{html.escape(answer.text)}</p>
    <div class="actions">
      <a class="button primary" href="/contracts">Contracts</a>
      <a class="button" href="/traces">Traces</a>
      <a class="button" href="/docs">API docs</a>
      {trace_link}
    </div>
  </div>
  <div class="card">
    <h2>Computed</h2>
    {figures or "<p class='hash'>No figures.</p>"}
  </div>
  <div class="card">
    <h2>Citations</h2>
    <ul>{citations or "<li class='hash'>none</li>"}</ul>
    <div class="fact"><span>context sha256</span><code class="hash">{hash_first}</code>
      <small class="{repeat_class}">re-run: {repeat}</small></div>
  </div>
  <div class="card" style="grid-column:1/-1">
    <h2>Provenance</h2>
    <pre class="mermaid">{html.escape(mermaid)}</pre>
    <details><summary>Mermaid source</summary><pre class="hash">{html.escape(mermaid)}</pre></details>
  </div>
</section>
</main>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/+esm";
mermaid.initialize({{
  startOnLoad: true,
  theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default"
}});
</script>
</body>
</html>"""


def build_demo_app() -> Any:
    """Builds the in-memory demo app (runs the demo once)."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    globals().setdefault("HTMLResponse", HTMLResponse)
    globals().setdefault("JSONResponse", JSONResponse)
    store = TraceStore(":memory:")
    tracer = Tracer(store=store)
    context = asyncio.run(run_demo(tracer=tracer))
    second = asyncio.run(run_demo())
    first_hash = context_hash(context)
    second_hash = context_hash(second)
    items = asyncio.run(store.query(limit=1))["items"]
    trace_id = str(items[0]["id"]) if items else ""

    answer = context.latest(Answer)
    spend = context.latest(Spend)
    variance = context.latest(Variance)
    if answer is None:
        raise RuntimeError("demo produced no Answer artifact")

    app = FastAPI(
        title="reactifact demo",
        description="A computed, cited, reproducible answer — no API key.",
    )
    install_request_id(app)
    install_error_handlers(app)
    app.include_router(
        create_contracts_router(describe_contracts(normalize_units(steps=STEPS)))
    )
    app.include_router(create_trace_router(store))

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _page(
            answer=answer.data,
            spend=spend.data if spend is not None else None,
            variance=variance.data if variance is not None else None,
            hash_first=first_hash,
            hash_second=second_hash,
            trace_id=trace_id,
            mermaid=context_to_mermaid(context),
        )

    @app.get("/api/demo")
    async def demo_json() -> JSONResponse:
        return JSONResponse(
            {
                "answer": answer.data.model_dump(mode="json"),
                "spend": spend.data.model_dump(mode="json") if spend else None,
                "variance": variance.data.model_dump(mode="json") if variance else None,
                "hash": first_hash,
                "rerun_hash": second_hash,
                "reproducible": first_hash == second_hash,
                "trace_id": trace_id,
            }
        )

    return app


def main_web(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> int:
    """Serve the demo app; returns a process exit code."""
    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            'demo --web needs uvicorn; install it with `uv add "reactifact[web]"`'
        ) from None

    app = build_demo_app()
    base = f"http://{host}:{port}"
    print("reactifact demo --web")
    print(f"  demo       {base}/")
    print(f"  contracts  {base}/contracts")
    print(f"  traces     {base}/traces")
    print(f"  api docs   {base}/docs")
    print(flush=True)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"{base}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


__all__ = ["build_demo_app", "main_web"]
