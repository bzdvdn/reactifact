"""A SQLite session that survives a restart, resumes and replays."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from reactifact import Budget, Effects, Runtime, Step, create_agent
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.replay import replay_context
from reactifact.session import SessionStore


class Job(BaseModel):
    text: str


class Done(BaseModel):
    text: str


class FinishJob(Step):
    consumes = (Job,)
    produces = (Done,)

    async def run(self, job: Job, effects: Effects) -> None:
        effects.create(Done(text=job.text.upper()), id="done:1")


async def _run_durable(path: Path) -> dict[str, Any]:
    backend = SQLiteKVBackend(str(path))
    store = SessionStore(backend)
    try:
        session = await store.open("tour")
        if not session.loaded:
            session.context.create(Job(text="survive a restart"), id="job:1")
            await session.save()
    finally:
        await backend.aclose()

    backend = SQLiteKVBackend(str(path))
    store = SessionStore(backend)
    try:
        session = await store.open("tour")
        loaded = session.loaded
        runtime = Runtime(
            session.context,
            agents=[create_agent("durable", steps=[FinishJob()])],
            session=session,
            budget=Budget(max_runs=10),
        )
        await runtime.arun()
        done = session.context.latest(Done)
        assert done is not None
        replay = await replay_context(store, "tour", version=1)
        replayed = replay.latest(Done)
        return {
            "loaded": loaded,
            "done": done.data.text,
            "replayed_done": replayed.data.text if replayed is not None else None,
            "version": session.context.version,
        }
    finally:
        await backend.aclose()


async def run_durable(path: str | Path | None = None) -> dict[str, Any]:
    if path is not None:
        return await _run_durable(Path(path))
    with tempfile.TemporaryDirectory() as directory:
        return await _run_durable(Path(directory) / "tour.sqlite3")
