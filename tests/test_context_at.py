import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Runtime
from reactifact.step import Step


class A(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class Make(Step):
    consumes = (A,)
    produces = (Out,)

    async def run(self, a: A) -> None:
        self.effects.upsert(Out(text=a.text), id="out")


class Flow(Agent):
    steps = [Make()]


def test_artifact_and_context_immutable_version_reads():
    context = Context()
    a = context.create(A(text="one"))
    runtime = Runtime(context, agents=[Flow()])
    asyncio.run(runtime.arun())
    first_version = context.version

    context.update(a.id, A(text="two"))
    asyncio.run(runtime.arun())
    second_version = context.version

    live = context.get("out")
    assert live is not None
    assert live.data.text == "two"
    assert live.at(0).text == "one"
    assert live.at(1).text == "two"

    snapshot = context.at(first_version)
    assert snapshot.version == first_version
    assert snapshot.get("out").data.text == "one"
    assert context.get("out").data.text == "two"  # live unchanged

    latest = context.at(second_version)
    assert latest.get("out").data.text == "two"


def test_content_addressed_artifact_versions():
    context = Context()
    a = context.create(A(text="one"))
    runtime = Runtime(context, agents=[Flow()])
    asyncio.run(runtime.arun())
    context.update(a.id, A(text="two"))
    asyncio.run(runtime.arun())

    versions = context.version_history("out")
    assert [v.data.text for v in versions] == ["one", "two"]
    assert versions[0].content_hash != versions[1].content_hash
    assert all(v.commit_id for v in versions)

    first = context.artifact_version("out", 0)
    assert first is not None
    assert first.data.text == "one"
    assert first.content_hash == versions[0].content_hash

    # The commit log itself carries the content hash of each write.
    commits = [
        c for c in context.history() if any(w.artifact_id == "out" for w in c.writes)
    ]
    assert all(
        w.content_hash is not None
        for c in commits
        for w in c.writes
        if w.artifact_id == "out"
    )
