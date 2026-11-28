import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime
from reactifact.events import EventType


class Document(BaseModel):
    title: str
    content: str


class DerivedSummary(BaseModel):
    text: str


class StalenessNotice(BaseModel):
    watched_id: str


class Summarizer(Agent):
    """Deterministic derived artifact: dependencies are visible through reads."""

    consumes = [Consume(Document)]
    produces = []

    async def run(self, event, context):
        doc = context.get(event.artifact_id)
        if doc is None:
            return None
        summary = DerivedSummary(text=doc.data.content.upper())
        # stable id: re-derivation updates the same entity, no duplicate
        return Patch().create(summary, id=f"summary:{doc.id}")


def build_doc_pipeline():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Summarizer()])
    doc = ctx.create(Document(title="T", content="hello world"))
    asyncio.run(runtime.arun())
    return ctx, runtime, doc


class StaleWatcher(Agent):
    """Reacts specifically to ARTIFACT_STALE — proves staleness is a live event,
    not just a `stale_artifacts()` poll."""

    consumes = [Consume(DerivedSummary, event_types=[EventType.ARTIFACT_STALE])]
    produces = []

    async def run(self, event, context):
        return Patch().create(
            StalenessNotice(watched_id=event.artifact_id),
            id=f"notice:{event.artifact_id}",
        )


def test_derived_artifact_and_reads_provenance():
    ctx, _runtime, doc = build_doc_pipeline()

    summary = ctx.list_artifacts(DerivedSummary)[0]
    assert summary.data.text == "HELLO WORLD"

    commit = ctx.history()[-1]
    # parent relation built by the runtime from consumes: summary read document@v0
    assert any(r.artifact_id == doc.id and r.version == 0 for r in commit.reads)
    assert any(w.artifact_id == summary.id for w in commit.writes)


def test_stale_detection_and_fresh_rerun(tmp_path):
    ctx, runtime, doc = build_doc_pipeline()
    summary = ctx.list_artifacts(DerivedSummary)[0]
    version_before_rerun = summary.version
    assert not ctx.has_stale()  # everything is fresh initially

    # source updated (new revision) — the derived artifact is stale
    ctx.update(doc.id, Document(title="T", content="new content"))
    stale = ctx.stale_artifacts()
    assert ctx.has_stale()
    assert any(a.id == summary.id for a in stale)

    # re-derivation: upsert by stable id, no duplicates, staleness resolved
    asyncio.run(runtime.arun())
    updated = ctx.list_artifacts(DerivedSummary)
    assert len(updated) == 1
    assert updated[0].id == summary.id
    assert updated[0].data.text == "NEW CONTENT"
    assert not ctx.has_stale()
    assert (
        updated[0].version == version_before_rerun + 1
    )  # a new revision, not a new object


def test_staleness_survives_session_restart(tmp_path):
    asyncio.run(_test_staleness_survives_session_restart(tmp_path))


async def _test_staleness_survives_session_restart(tmp_path):
    from reactifact import RuntimeResources, SessionStore
    from reactifact.checkpoints import FileKVBackend

    store = SessionStore(FileKVBackend(str(tmp_path)))
    session = await store.open("inval", resources=RuntimeResources())
    runtime = Runtime(session.context, agents=[Summarizer()], session=session)

    doc = session.context.create(Document(title="T", content="v1"))
    await runtime.arun()
    await session.save()

    # restart: update the source, save, reload again — staleness is visible
    session.context.update(doc.id, Document(title="T", content="v2"))
    await session.save()

    session2 = await store.open("inval", resources=RuntimeResources())
    assert session2.context.has_stale()
    stale_ids = [a.id for a in session2.context.stale_artifacts()]
    assert stale_ids and stale_ids[0].startswith("summary:")


def test_stale_event_is_reactive():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Summarizer(), StaleWatcher()])
    doc = ctx.create(Document(title="T", content="hello world"))
    asyncio.run(runtime.arun())
    summary = ctx.list_artifacts(DerivedSummary)[0]
    assert not ctx.list_artifacts(StalenessNotice)

    ctx.update(doc.id, Document(title="T", content="new content"))
    asyncio.run(runtime.arun())

    notices = ctx.list_artifacts(StalenessNotice)
    assert any(n.data.watched_id == summary.id for n in notices)
