import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, EventType, Patch, Runtime, Trigger
from reactifact.commit import Read, Write


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    content: str


class Answer(BaseModel):
    text: str


class Researcher(Agent):
    consumes = [Consume(Question)]
    produces = []

    async def run(self, event, context):
        q = context.get(event.artifact_id)
        if q is None:
            return None
        return Patch().create(Evidence(content=q.data.text))


class Answerer(Agent):
    consumes = [Consume(Evidence)]
    produces = []

    async def run(self, event, context):
        ev = context.get(event.artifact_id)
        if ev is None:
            return None
        return Patch().create(Answer(text=ev.data.content.upper()))


def build_runtime():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Researcher(), Answerer()])
    question = ctx.create(Question(text="why"))
    asyncio.run(runtime.arun())
    return ctx, runtime, question


def test_commit_chain_and_provenance():
    ctx, _runtime, question = build_runtime()

    assert ctx.version == 2
    commits = ctx.history()
    assert len(commits) == 2
    first, second = commits

    assert first.parent_id is None
    assert second.parent_id == first.id
    assert first.context_version == 1
    assert second.context_version == 2


def test_reads_writes_recorded_from_consumes():
    ctx, _runtime, question = build_runtime()
    first, second = ctx.history()

    assert first.reads == [Read(question.id, 0)]
    assert second.reads == [Read(ctx.list_artifacts(Evidence)[0].id, 0)]

    assert first.writes == [Write(ctx.list_artifacts(Evidence)[0].id, 0, "create")]
    assert second.writes == [Write(ctx.list_artifacts(Answer)[0].id, 0, "create")]


def test_created_by_commit_stamp():
    ctx, _runtime, question = build_runtime()
    first, second = ctx.history()

    assert ctx.list_artifacts(Evidence)[0].created_by_commit == first.id
    assert ctx.list_artifacts(Answer)[0].created_by_commit == second.id
    # an artifact created directly (outside commits) has no stamp
    assert question.created_by_commit is None


def test_history_diff_snapshot():
    ctx, _runtime, question = build_runtime()
    evidence = ctx.list_artifacts(Evidence)[0]
    answer = ctx.list_artifacts(Answer)[0]

    d1 = ctx.diff(0, 1)
    assert set(d1["added"].keys()) == {evidence.id}

    d2 = ctx.diff(0, 2)
    assert set(d2["added"].keys()) == {evidence.id, answer.id}

    snap = ctx.snapshot()
    assert set(snap.keys()) == {question.id, evidence.id, answer.id}


def test_checkout_rollback():
    ctx, _runtime, question = build_runtime()
    evidence = ctx.list_artifacts(Evidence)[0]
    answer = ctx.list_artifacts(Answer)[0]

    ctx.checkout(1)

    assert ctx.version == 1
    assert ctx.get(question.id) is not None  # working tree is preserved
    assert ctx.get(evidence.id) is not None
    assert ctx.get(answer.id) is None  # rollback removed the descendant
    assert len(ctx.history()) == 1


def test_checkpoint_roundtrip_preserves_head():
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        ctx, _runtime, question = build_runtime()
        path = os.path.join(tmpdir, "checkpoint.json")
        asyncio.run(ctx.save_checkpoint(path))

        ctx2 = asyncio.run(Context.load_checkpoint(path))

        assert ctx2.version == ctx.version
        assert ctx2.head_id == ctx.head_id
        assert len(ctx2.history()) == 2
        assert ctx2.history()[1].parent_id == ctx2.history()[0].id
        assert ctx2.list_artifacts(Answer)[0].created_by_commit == ctx2.history()[1].id
        assert ctx2.list_artifacts(Answer)[0].data.text == "WHY"


def test_reactive_doubling_provenance():
    class Number(BaseModel):
        value: int

    class Doubler(Agent):
        def __init__(self):
            super().__init__(
                name="doubler",
                triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)],
            )

        async def run(self, event, context):
            art = context.get(event.artifact_id)
            if art is None:
                return None
            return Patch().update(art.id, Number(value=art.data.value * 2))

    ctx = Context()
    runtime = Runtime(ctx, agents=[Doubler()])
    number = ctx.create(Number(value=5))
    assert asyncio.run(runtime.arun()) == 1

    commit = ctx.history()[0]
    assert commit.context_version == 1
    # reads record the trigger artifact even without declared consumes
    assert commit.reads == [Read(number.id, 0)]
    # writes record the update of the same revision
    assert commit.writes == [Write(number.id, 1, "update")]
