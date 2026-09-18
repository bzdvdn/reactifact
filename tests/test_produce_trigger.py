import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Event, EventType, Produce, Runtime
from reactifact.produce import produce


class ResolvedDocuments(BaseModel):
    query_id: str
    text: str


class DecisionReply(BaseModel):
    query_id: str
    route_action: str


class Marker(BaseModel):
    tag: str


class DraftAnswer(BaseModel):
    query_id: str
    source: str


class FinalizeWithDocuments(Produce[DraftAnswer]):
    """`reacts_to` set: `call.trigger` is guaranteed to be the live,
    correctly-typed artifact — no guard at all."""

    artifact_type = DraftAnswer
    reacts_to = (ResolvedDocuments,)

    async def produce(self, call):
        trigger = call.trigger
        assert trigger is not None
        self.effects.create(DraftAnswer(query_id=trigger.data.query_id, source="documents"))


class TwoTypeReactor(Produce[DraftAnswer]):
    """`reacts_to` lists two types — `call.trigger` is whichever one actually
    fired, resolved per call, not a union collapsed at declaration time."""

    artifact_type = DraftAnswer
    reacts_to = (ResolvedDocuments, DecisionReply)

    async def produce(self, call):
        trigger = call.trigger
        assert trigger is not None
        kind = type(trigger.data).__name__
        self.effects.create(DraftAnswer(query_id=trigger.data.query_id, source=kind))


class BestEffortReactor(Produce[DraftAnswer]):
    """No `reacts_to` — `call.trigger` is still resolved on a best-effort
    basis, but never gates whether `produce()` runs at all."""

    artifact_type = DraftAnswer

    async def produce(self, call):
        trigger = call.trigger
        source = "none" if trigger is None else type(trigger.data).__name__
        self.effects.create(DraftAnswer(query_id="q", source=source))


class FinalAgent(Agent):
    consumes = [Consume(ResolvedDocuments)]
    produces = [FinalizeWithDocuments()]


def test_reacts_to_with_trigger_needs_no_guard():
    ctx = Context()
    runtime = Runtime(ctx, agents=[FinalAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.query_id == "q1"
    assert drafts[0].data.source == "documents"


class TwoTypeAgent(Agent):
    consumes = [Consume(ResolvedDocuments), Consume(DecisionReply)]
    produces = [TwoTypeReactor()]


def test_reacts_to_with_several_types_resolves_whichever_fired():
    ctx = Context()
    runtime = Runtime(ctx, agents=[TwoTypeAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())
    ctx.create(DecisionReply(query_id="q2", route_action="final"))
    asyncio.run(runtime.arun())

    drafts = {d.data.query_id: d.data.source for d in ctx.list_artifacts(DraftAnswer)}
    assert drafts == {"q1": "ResolvedDocuments", "q2": "DecisionReply"}


class BestEffortAgent(Agent):
    consumes = [Consume(ResolvedDocuments)]
    produces = [BestEffortReactor()]


def test_trigger_without_reacts_to_is_best_effort_not_gating():
    ctx = Context()
    runtime = Runtime(ctx, agents=[BestEffortAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.source == "ResolvedDocuments"


class DeletionAware(Produce[Marker]):
    """A produce reacting to its own type's DELETED event: `call.trigger` is
    correctly `None` there (that *is* the event) — declared only to prove
    `_resolve_produce_call` still says "run" for it, not exercised through a
    live runtime (see the unit tests below for why)."""

    artifact_type = Marker
    reacts_to = (Marker,)

    async def produce(self, call):
        self.effects.create(Marker(tag="deleted" if call.trigger is None else "alive"))


# The remaining two cases — an agent run with no event at all (the
# scheduler-driven path), and an event whose artifact was deleted earlier in
# the same generation (the race `Trigger.matches()` documents) — are
# exercised directly against `Agent._resolve_produce_call`. Both need a
# `Context`/`Event` combination that a normal sequential `Runtime.arun()`
# can't produce on its own (patches within one generation only apply once
# every agent in it has finished), and neither needs the full effects/patch
# machinery `produce()` itself depends on.


def test_resolve_produce_call_trigger_is_none_without_event():
    """The scheduler-driven path (`Runtime` calling `agent.execute(context)`
    with no specific triggering event) — nothing to resolve `trigger` from,
    but a produce with no `reacts_to` still runs."""
    ctx = Context()
    runs, trigger = Agent._resolve_produce_call(BestEffortReactor(), None, ctx)
    assert runs is True
    assert trigger is None


def test_resolve_produce_call_skips_when_the_artifact_was_deleted_meanwhile():
    """The race `reacts_to` accounts for: the event still names an artifact
    id, but something else deleted it earlier in the same generation —
    `FinalizeWithDocuments` (which relies on `call.trigger` never being
    `None`) must not be called at all, not called with `trigger=None`."""
    ctx = Context()
    doc = ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    ctx.delete(doc.id)
    stale_event = Event(EventType.ARTIFACT_CREATED, ResolvedDocuments, doc.id)

    runs, trigger = Agent._resolve_produce_call(
        FinalizeWithDocuments(), stale_event, ctx
    )
    assert runs is False
    assert trigger is None


def test_resolve_produce_call_deleted_event_still_runs_with_trigger_none():
    """Exempt from the liveness requirement above: a DELETED event on the
    produce's own `reacts_to` type — `context.get(...)` returning `None`
    *is* the event there, not a race, so the call still happens."""
    ctx = Context()
    marker = ctx.create(Marker(tag="original"))
    ctx.delete(marker.id)
    deleted_event = Event(EventType.ARTIFACT_DELETED, Marker, marker.id)

    runs, trigger = Agent._resolve_produce_call(DeletionAware(), deleted_event, ctx)
    assert runs is True
    assert trigger is None


@produce(DraftAnswer, reacts_to=(ResolvedDocuments,))
def decorated_with_trigger(call):
    return DraftAnswer(query_id=call.trigger.data.query_id, source="decorated")


class DecoratedAgent(Agent):
    consumes = [Consume(ResolvedDocuments)]
    produces = [decorated_with_trigger]


def test_produce_decorator_accepts_trigger():
    ctx = Context()
    runtime = Runtime(ctx, agents=[DecoratedAgent()])

    ctx.create(ResolvedDocuments(query_id="q1", text="doc"))
    asyncio.run(runtime.arun())

    drafts = ctx.list_artifacts(DraftAnswer)
    assert len(drafts) == 1
    assert drafts[0].data.source == "decorated"
