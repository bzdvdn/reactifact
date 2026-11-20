"""Retries and deadlines: an agent that fails once, and a step that hangs."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel
from reactifact import (
    Context,
    RetryPolicy,
    Runtime,
    Step,
    create_agent,
)


class Job(BaseModel):
    text: str


class Done(BaseModel):
    text: str


class FlakyStep(Step):
    consumes = (Job,)
    produces = (Done,)

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def run(self, job: Job) -> Done:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient upstream failure")
        return Done(text=job.text.upper())


class SlowStep(Step):
    consumes = (Job,)
    produces = (Done,)
    timeout = 0.01

    async def run(self, job: Job) -> Done:
        await asyncio.sleep(0.2)
        return Done(text=job.text)


async def run_resilience() -> dict[str, Any]:
    flaky = FlakyStep()
    context = Context()
    context.create(Job(text="retry me"), id="job:retry")
    runtime = Runtime(
        context,
        agents=[
            create_agent(
                "flaky",
                steps=[flaky],
                retry=RetryPolicy(max_attempts=2, backoff=0),
            )
        ],
    )
    started = time.monotonic()
    await runtime.arun()
    retry_ms = (time.monotonic() - started) * 1000

    slow_context = Context()
    slow_context.create(Job(text="too slow"), id="job:slow")
    timed_out = False
    try:
        await Runtime(
            slow_context,
            agents=[create_agent("slow", steps=[SlowStep()])],
        ).arun()
    except TimeoutError:
        timed_out = True

    done = context.latest(Done)
    return {
        "attempts": flaky.calls,
        "answer": done.data.text if done is not None else None,
        "retry_ms": round(retry_ms, 1),
        "timed_out": timed_out,
        "artifacts_after_timeout": len(slow_context.list_artifacts(Done)),
    }
