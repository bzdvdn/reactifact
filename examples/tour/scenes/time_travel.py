"""Time travel: list checkpoints, snapshot the past, and fork a session."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Consume, Runtime, SessionStore, Step
from reactifact.checkpoints import FileKVBackend


class Job(BaseModel):
    text: str


class Done(BaseModel):
    text: str


class Finish(Step):
    reads = (Job,)
    produces = (Done,)

    async def run(self, job: Job) -> Done:
        return Done(text=job.text.upper())


async def _run_time_travel(directory: Path) -> dict[str, Any]:
    store = SessionStore(FileKVBackend(str(directory / "sessions")))
    session = await store.open("time-travel")
    runtime = Runtime(
        session.context,
        agents=[Agent("finish", consumes=[Consume(Job)], steps=[Finish()])],
        session=session,
    )

    session.context.create(Job(text="first"), id="job:1")
    await runtime.arun()
    session.context.create(Job(text="second"), id="job:2")
    await runtime.arun()

    checkpoints = session.checkpoints()
    first = checkpoints[0]
    snapshot = session.context.at(first)
    fork = await store.fork("time-travel", new_id="time-travel-fork", at=first.id)

    live_done = session.context.latest(Done)
    old_done = snapshot.latest(Done)
    fork_done = fork.context.latest(Done)
    return {
        "versions": [checkpoint.version for checkpoint in checkpoints],
        "first_author": first.author,
        "live": live_done.data.text if live_done is not None else None,
        "snapshot": old_done.data.text if old_done is not None else None,
        "fork_version": fork.context.version,
        "fork": fork_done.data.text if fork_done is not None else None,
    }


async def run_time_travel(directory: str | Path | None = None) -> dict[str, Any]:
    if directory is not None:
        return await _run_time_travel(Path(directory))
    with tempfile.TemporaryDirectory() as tmp:
        return await _run_time_travel(Path(tmp))
