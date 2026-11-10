"""Versions, history, diff and compaction on a small typed artifact."""

from __future__ import annotations

from typing import Annotated, Any, cast

from pydantic import BaseModel
from reactifact import (
    Artifact,
    Budget,
    Context,
    Effects,
    Runtime,
    Step,
    When,
    create_agent,
)


class Seed(BaseModel):
    text: str


class Draft(BaseModel):
    text: str
    revision: int = 0


class StartDraft(Step):
    consumes = (Seed,)
    produces = (Draft,)

    async def run(self, seed: Seed, effects: Effects) -> None:
        effects.create(Draft(text=seed.text), id="draft:1")


class PolishDraft(Step):
    consumes = (Annotated[Draft, When(lambda draft: draft.revision < 2)],)
    produces = (Draft,)

    async def run(self, draft: Artifact[Draft], effects: Effects) -> None:
        effects.update(
            draft,
            text=draft.data.text + "!",
            revision=draft.data.revision + 1,
        )


async def run_state() -> dict[str, Any]:
    context = Context()
    context.create(Seed(text="versioned artifacts"), id="seed:1")
    runtime = Runtime(
        context,
        agents=[create_agent("state", steps=[StartDraft(), PolishDraft()])],
        budget=Budget(max_runs=20),
    )
    await runtime.arun()

    draft = context.latest(Draft)
    assert draft is not None
    versions = context.version_history("draft:1")
    historical = context.at(1).get("draft:1")
    diff = context.diff(max(1, context.version - 1), context.version)
    dropped = context.compact(keep_commits=1, keep_versions=1)
    kept = context.version_history("draft:1")

    return {
        "current": draft.data.text,
        "revision": draft.data.revision,
        "versions": [cast(Draft, version.data).revision for version in versions],
        "historical_revision": (
            cast(Draft, historical.data).revision if historical is not None else None
        ),
        "changed": sorted(diff["changed"]),
        "compaction_dropped": dropped,
        "versions_after_compaction": [
            cast(Draft, version.data).revision for version in kept
        ],
    }
