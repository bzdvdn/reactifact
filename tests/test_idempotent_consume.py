"""`IdempotentConsume`: wake only while the derived target id is absent."""

import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Emit, Runtime, Step
from reactifact.consume import IdempotentConsume


class Topic(BaseModel):
    text: str


class Draft(BaseModel):
    text: str


class MakeDraft(Step[Draft]):
    artifact_type = Draft
    reads = (IdempotentConsume(Topic, id_of=lambda topic: f"draft:{topic.id}"),)

    async def run(self, call):
        topic = call.trigger
        if topic is None:
            return None
        return Emit(Draft(text=topic.data.text), id=f"draft:{topic.id}")


class Flow(Agent):
    consumes = [Consume(Topic)]
    steps = [MakeDraft()]


def test_fires_while_the_target_is_absent():
    ctx = Context()
    topic = ctx.create(Topic(text="first"), id="topic:1")
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())

    drafts = ctx.list_artifacts(Draft)
    assert len(drafts) == 1
    assert drafts[0].id == "draft:topic:1"
    assert drafts[0].data.text == topic.data.text


def test_does_not_fire_once_the_target_exists():
    ctx = Context()
    ctx.create(Topic(text="first"), id="topic:1")
    runtime = Runtime(ctx, agents=[Flow()])
    asyncio.run(runtime.arun())

    first = ctx.list_artifacts(Draft)[0]
    version = first.version
    ctx.update("topic:1", Topic(text="second"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(Draft)
    assert len(drafts) == 1
    assert drafts[0].version == version  # no refresh, no re-run
    assert drafts[0].data.text == "first"


def test_id_of_none_never_fires():
    class Skip(Step[Draft]):
        artifact_type = Draft
        reads = (
            IdempotentConsume(
                Topic,
                id_of=lambda topic: None,
            ),
        )

        async def run(self, call):
            return Emit(Draft(text="never"), id="draft:never")

    class SkipFlow(Agent):
        consumes = [Consume(Topic)]
        steps = [Skip()]

    ctx = Context()
    ctx.create(Topic(text="x"), id="topic:1")
    asyncio.run(Runtime(ctx, agents=[SkipFlow()]).arun())

    assert ctx.list_artifacts(Draft) == []


def test_wakes_false_has_no_triggers():
    consume = IdempotentConsume(
        Topic,
        id_of=lambda topic: f"draft:{topic.id}",
        wakes=False,
    )
    assert consume.to_triggers() == []
    assert consume.collect(Context()) == []


def test_condition_narrows_the_trigger():
    class Conditional(Step[Draft]):
        artifact_type = Draft
        reads = (
            IdempotentConsume(
                Topic,
                id_of=lambda topic: f"draft:{topic.id}",
                condition=lambda topic: topic.data.text == "go",
            ),
        )

        async def run(self, call):
            topic = call.trigger
            assert topic is not None
            return Emit(Draft(text=topic.data.text), id=f"draft:{topic.id}")

    class ConditionalFlow(Agent):
        consumes = [Consume(Topic)]
        steps = [Conditional()]

    ctx = Context()
    ctx.create(Topic(text="no"), id="topic:no")
    ctx.create(Topic(text="go"), id="topic:go")
    asyncio.run(Runtime(ctx, agents=[ConditionalFlow()]).arun())

    assert [d.id for d in ctx.list_artifacts(Draft)] == ["draft:topic:go"]


def test_collect_excludes_topics_whose_target_exists():
    consume = IdempotentConsume(
        Topic,
        id_of=lambda topic: f"draft:{topic.id}",
    )
    ctx = Context()
    blocked = ctx.create(Topic(text="old"), id="topic:old")
    fresh = ctx.create(Topic(text="new"), id="topic:new")
    ctx.create(Draft(text="done"), id="draft:topic:old")

    assert consume.collect(ctx) == [fresh]
    assert blocked not in consume.collect(ctx)
