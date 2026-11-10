"""Typed inputs, non-waking reads and debounced fan-out."""

from __future__ import annotations

from typing import Annotated, Any, cast

from pydantic import BaseModel
from reactifact import (
    Artifact,
    Budget,
    Consume,
    Context,
    Effects,
    Latest,
    Runtime,
    Step,
    Wakes,
    create_agent,
)


class Topic(BaseModel):
    name: str


class Policy(BaseModel):
    max_facts: int


class Fact(BaseModel):
    topic: str
    text: str


class Digest(BaseModel):
    topic: str
    facts: list[str]
    max_facts: int


class FanOutFacts(Step):
    consumes = (Topic,)
    produces = (Fact,)

    async def run(self, topic: Topic, effects: Effects) -> None:
        effects.create(Fact(topic=topic.name, text="typed inputs"), id="fact:1")
        effects.create(Fact(topic=topic.name, text="debounced fan-out"), id="fact:2")
        effects.create(Fact(topic=topic.name, text="scoped history"), id="fact:3")


class CollectFacts(Step):
    consumes = (
        Consume(Fact, debounce=True),
        Annotated[Policy, Wakes(False)],
    )
    produces = (Digest,)

    async def run(
        self,
        facts: list[Artifact[Fact]],
        policy: Policy,
        topic: Latest[Topic],
        effects: Effects,
    ) -> None:
        effects.create(
            Digest(
                topic=cast(Topic, topic).name,
                facts=[fact.data.text for fact in facts],
                max_facts=policy.max_facts,
            ),
            id="digest:1",
        )


async def run_reactive() -> dict[str, Any]:
    context = Context()
    context.create(Topic(name="durability"), id="topic:1")
    context.create(Policy(max_facts=3), id="policy:1")
    runtime = Runtime(
        context,
        agents=[create_agent("reactive", steps=[FanOutFacts(), CollectFacts()])],
        budget=Budget(max_runs=20),
    )
    await runtime.arun()

    digest = context.latest(Digest)
    assert digest is not None
    return {
        "digest_count": len(context.list_artifacts(Digest)),
        "facts": digest.data.facts,
        "topic": digest.data.topic,
        "max_facts": digest.data.max_facts,
    }
