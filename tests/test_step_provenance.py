"""`@step(provenance=...)` — edges from what the step actually read."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    SUPPORTED_BY,
    Agent,
    Artifact,
    Budget,
    Context,
    Effects,
    Runtime,
    step,
)


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@step(produces=Output, consumes=Input, provenance=True)
def auto(input: Input) -> Output:
    return Output(value=input.value + 1)


@step(produces=Output, consumes=Input)
def manual(input: Input) -> Output:
    return Output(value=input.value + 1)


@step(produces=Output, consumes=Input, provenance=SUPPORTED_BY)
def named_relation(input: Input) -> Output:
    return Output(value=input.value + 1)


@step(produces=Output, consumes=Input, provenance=True)
def explicit(input: Artifact[Input], effects: Effects) -> None:
    handle = effects.create(Output(value=input.data.value))
    assert handle is not None
    handle.link("custom", input)
    return None


def run(agents, seed: list[tuple[str, int]]) -> Context:
    context = Context()
    runtime = Runtime(context, groups=agents, budget=Budget(max_runs=10))
    for artifact_id, value in seed:
        context.create(Input(value=value), id=artifact_id)
    asyncio.run(runtime.arun())
    return context


def edges(context: Context, artifact_type: type[BaseModel]) -> list[tuple[str, str]]:
    artifact = context.latest(artifact_type)
    assert artifact is not None
    return [
        (rel.relation, rel.target_id)
        for rel in context.relations(source_id=artifact.id)
    ]


def test_provenance_points_at_the_input_the_step_resolved():
    context = run([Agent("a", steps=[auto])], [("in", 1)])
    assert edges(context, Output) == [("derived_from", "in")]


def test_provenance_off_by_default():
    context = run([Agent("a", steps=[manual])], [("in", 1)])
    assert edges(context, Output) == []


def test_provenance_accepts_a_relation_constant():
    context = run([Agent("a", steps=[named_relation])], [("in", 1)])
    assert edges(context, Output) == [("supported_by", "in")]


def test_provenance_uses_the_latest_match_not_every_input():
    context = run(
        [Agent("a", steps=[auto])],
        [("old", 1), ("new", 2)],
    )
    assert edges(context, Output) == [("derived_from", "new")]


def test_explicit_link_wins_over_provenance():
    context = run([Agent("a", steps=[explicit])], [("in", 1)])
    assert edges(context, Output) == [("custom", "in")]
