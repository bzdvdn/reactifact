import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, Runtime
from reactifact.produce import produce


class ResolvedDocuments(BaseModel):
    query_id: str
    text: str


class DecisionReply(BaseModel):
    query_id: str
    route_action: str
    text: str


class DraftAnswer(BaseModel):
    query_id: str
    source: str


class FinalizeWithDocuments(Produce[DraftAnswer]):
    """Same output type as DirectFinalize below, but reacts only to
    ResolvedDocuments — the exact case that rules out reusing
    `artifact_type` for both directions."""

    artifact_type = DraftAnswer
    reacts_to = (ResolvedDocuments,)

    async def produce(self, call):
        self.effects.create(
            DraftAnswer(query_id=call.trigger.data.query_id, source="documents")
        )


class DirectFinalize(Produce[DraftAnswer]):
    artifact_type = DraftAnswer
    reacts_to = (DecisionReply,)

    async def produce(self, call):
        self.effects.create(
            DraftAnswer(query_id=call.trigger.data.query_id, source="direct")
        )


class FinalAgent(Agent):
    consumes = [
        Consume(ResolvedDocuments),
        Consume.by_field(DecisionReply, "route_action", "final"),
    ]
    produces = [FinalizeWithDocuments(), DirectFinalize()]


def test_each_produce_only_runs_for_its_own_reacts_to_type():
    ctx = Context()
    runtime = Runtime(ctx, agents=[FinalAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.source == "documents"


def test_the_other_reacts_to_type_runs_the_other_produce_only():
    ctx = Context()
    runtime = Runtime(ctx, agents=[FinalAgent()])

    ctx.create(DecisionReply(query_id="q1", route_action="final", text="reply"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.source == "direct"


class UnrestrictedRecorder(Produce[DraftAnswer]):
    """No `reacts_to` at all — must run on every matching event, unchanged
    from pre-`reacts_to` behavior."""

    artifact_type = DraftAnswer

    async def produce(self, call):
        self.effects.create(DraftAnswer(query_id="q1", source="unrestricted"))


class UnrestrictedAgent(Agent):
    consumes = [Consume(ResolvedDocuments), Consume(DecisionReply)]
    produces = [UnrestrictedRecorder()]


def test_reacts_to_none_is_unrestricted_by_default():
    ctx = Context()
    runtime = Runtime(ctx, agents=[UnrestrictedAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())
    ctx.create(DecisionReply(query_id="q1", route_action="x", text="reply"))
    asyncio.run(runtime.arun())

    assert len(ctx.list_artifacts(DraftAnswer)) == 2


class SingleTypeReactor(Produce[DraftAnswer]):
    """`reacts_to=` passed as a bare single type at the constructor call
    site (not a tuple) — the documented convenience shorthand."""

    artifact_type = DraftAnswer

    def __init__(self):
        super().__init__(reacts_to=ResolvedDocuments)

    async def produce(self, call):
        self.effects.create(DraftAnswer(query_id="q1", source="single-type"))


class SingleTypeAgent(Agent):
    consumes = [Consume(ResolvedDocuments), Consume(DecisionReply)]
    produces = [SingleTypeReactor()]


def test_reacts_to_accepts_a_bare_single_type_at_the_constructor():
    ctx = Context()
    runtime = Runtime(ctx, agents=[SingleTypeAgent()])

    ctx.create(DecisionReply(query_id="q1", route_action="x", text="reply"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(DraftAnswer) == []  # wrong type, didn't react

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())
    assert len(ctx.list_artifacts(DraftAnswer)) == 1


@produce(DraftAnswer, reacts_to=(ResolvedDocuments,))
def decorated_reactor(call):
    return DraftAnswer(query_id=call.trigger.data.query_id, source="decorated")


class DecoratedAgent(Agent):
    consumes = [Consume(ResolvedDocuments), Consume(DecisionReply)]
    produces = [decorated_reactor]


def test_produce_decorator_accepts_reacts_to():
    ctx = Context()
    runtime = Runtime(ctx, agents=[DecoratedAgent()])

    ctx.create(DecisionReply(query_id="q1", route_action="x", text="reply"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(DraftAnswer) == []  # wrong type, didn't react

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())
    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.source == "decorated"
