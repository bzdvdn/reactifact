"""Optimistic concurrency: a parallel-write conflict, then a merge policy."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import (
    Agent,
    ArtifactConflict,
    Context,
    MergeConflict,
    Read,
    Runtime,
    Step,
)
from reactifact.effects import current_effects


class Note(BaseModel):
    text: str


class Trigger(BaseModel):
    text: str


class Writer(Step):
    reads = (Read(Trigger),)
    produces = (Note,)

    def __init__(self, text: str):
        self.text = text

    async def run(self, trigger: Trigger, context: Context) -> None:
        note = context.get("note:1")
        assert note is not None
        effects = current_effects()
        assert effects is not None
        effects.update(
            note,
            expected_version=note.version,
            text=self.text,
        )


async def run_conflicts() -> dict[str, Any]:
    context = Context()
    context.create(Note(text="base"), id="note:1")
    context.create(Trigger(text="go"), id="trigger:1")
    runtime = Runtime(
        context,
        agents=[
            Agent("writer-a", steps=[Writer("a")]),
            Agent("writer-b", steps=[Writer("b")]),
        ],
        max_concurrency=2,
    )

    conflict_raised = False
    conflict_actual = None
    try:
        await runtime.arun()
    except ArtifactConflict as exc:
        conflict_raised = True
        conflict_actual = exc.actual

    base = Context()
    base.create(Note(text="base"), id="note:1")
    left = base.branch(name="left")
    right = base.branch(name="right")
    left.update("note:1", Note(text="left"))
    right.update("note:1", Note(text="right"))

    merge_conflict = False
    try:
        left.merge(right)
    except MergeConflict:
        merge_conflict = True

    left.merge(
        right,
        policies={Note: lambda a, b: Note(text=f"{a.text}+{b.text}")},
    )
    merged = left.get("note:1")
    return {
        "conflict_raised": conflict_raised,
        "conflict_actual": conflict_actual,
        "artifact_after": context.get("note:1").data.text,  # type: ignore[union-attr]
        "merge_conflict": merge_conflict,
        "merged": merged.data.text if merged is not None else None,
    }
