"""medic-lab web entry — the FastAPI app factory and the dev launcher.

Assembly only: sessions + trace store + API router + static UI templates.
The individual API endpoints live in `api/chat.py`; `build_resources` (llm and
sources) comes from `chat.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from reactifact import SessionStore
from reactifact.checkpoints import FileKVBackend
from reactifact.tracing import TraceStore
from reactifact.tracing.web import create_trace_router

from examples.medic_lab.agents import PIPELINE
from examples.medic_lab.api import create_router
from examples.medic_lab.chat import _UNSET, build_resources

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

UI_DIR = ROOT / "api" / "templates" / "web"


def create_app(store_dir: str | None = None, llm: Any = _UNSET) -> FastAPI:
    """App factory. `store_dir` — tests (temp sessions/traces); the default
    resolves the LLM from .env (`llm=_UNSET`). Pass `llm=None` to force the
    deterministic fallbacks in hermetic tests."""
    store = SessionStore(
        FileKVBackend(str(Path(store_dir) if store_dir else ROOT / "sessions"))
    )
    trace_store = TraceStore(
        str(Path(store_dir) / "traces.db") if store_dir else str(ROOT / "traces.db")
    )

    app = FastAPI(title="medic-lab (reactifact)")
    app.include_router(
        create_trace_router(
            trace_store,
            username=os.environ.get("TRACE_USER") or None,
            password=os.environ.get("TRACE_PASSWORD") or None,
        )
    )
    app.include_router(
        create_router(
            store=store,
            agents=PIPELINE.agents,
            resources_factory=lambda: build_resources(llm=llm),
            trace_store=trace_store,
        )
    )
    if UI_DIR.exists():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="web")
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8002)
