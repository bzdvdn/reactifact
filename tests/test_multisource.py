import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Patch,
    Runtime,
    RuntimeResources,
)
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


class TypedDoc(BaseModel):
    """Example of typed materialization: provenance is preserved (§34, §64)."""

    source_id: str
    path: str
    content: str


class SearchScout(Agent):
    """Fan-out across sources: each source searches in its own way (§8)."""

    consumes = [Consume(Question)]
    produces = []

    async def run(self, event, context):
        question = context.get(event.artifact_id)
        if question is None:
            return None
        patch = Patch()
        for source in context.resources.sources.values():
            for ref in source.search(question.data.text, limit=5):
                # stable id: a repeated search does not create duplicates (§42)
                patch.create(ref, id=ref.stable_id())
        return patch


class TypedResolver(Agent):
    """Materializes a lazy reference into a typed artifact."""

    consumes = [Consume(SourceRef)]
    produces = []

    async def run(self, event, context):
        ref_artifact = context.get(event.artifact_id)
        if ref_artifact is None:
            return None
        ref = ref_artifact.data
        source = context.resources.get_source(ref.source_id)
        if source is None:
            return None
        try:
            content = await source.resolve(ref)
        except Exception:
            return None
        doc = TypedDoc(source_id=ref.source_id, path=ref.locator, content=content)
        # the materialized copy has its own stable-id namespace (§42)
        return Patch().create(doc, id=f"resolved:{ref.stable_id()}")


def build_multisource(tmp_path):
    docs_a = tmp_path / "docs_a"
    docs_b = tmp_path / "docs_b"
    docs_a.mkdir()
    docs_b.mkdir()
    (docs_a / "auth.md").write_text(
        "Аутентификация через SSO и токены доступа.", encoding="utf-8"
    )
    (docs_b / "costs.md").write_text(
        "Стоимость платформы выросла из-за GPU-узлов.", encoding="utf-8"
    )
    sources = {
        "auth-docs": FileSystemSource(str(docs_a), source_id="auth-docs"),
        "cost-docs": FileSystemSource(str(docs_b), source_id="cost-docs"),
    }
    ctx = Context(resources=RuntimeResources(sources=sources))
    runtime = Runtime(ctx, agents=[SearchScout(), TypedResolver()])
    return ctx, runtime


def test_multisource_search_and_resolve(tmp_path):
    ctx, runtime = build_multisource(tmp_path)
    ctx.create(Question(text="стоимость гпу"))
    asyncio.run(runtime.arun())

    refs = ctx.list_artifacts(SourceRef)
    assert len(refs) == 1  # only cost-docs is relevant
    ref = refs[0].data
    assert ref.source_id == "cost-docs"
    assert ref.locator == "costs.md"
    assert ref.score is not None and ref.score > 0

    docs = ctx.list_artifacts(TypedDoc)
    assert len(docs) == 1
    assert docs[0].data.source_id == "cost-docs"
    assert "GPU" in docs[0].data.content
    assert docs[0].id == f"resolved:{ref.stable_id()}"


def test_multisource_all_sources_participate(tmp_path):
    ctx, runtime = build_multisource(tmp_path)
    ctx.create(Question(text="аутентификация"))
    asyncio.run(runtime.arun())

    refs = [r.data for r in ctx.list_artifacts(SourceRef)]
    assert len(refs) == 1
    assert refs[0].source_id == "auth-docs"


def test_scout_is_idempotent_across_questions(tmp_path):
    ctx, runtime = build_multisource(tmp_path)

    ctx.create(Question(text="стоимость"))
    asyncio.run(runtime.arun())
    refs_after_first = len(ctx.list_artifacts(SourceRef))
    asserts_docs_after_first = len(ctx.list_artifacts(TypedDoc))

    ctx.create(Question(text="цена"))
    asyncio.run(runtime.arun())

    assert len(ctx.list_artifacts(SourceRef)) == refs_after_first
    assert len(ctx.list_artifacts(TypedDoc)) == asserts_docs_after_first
    # a repeated run multiplied neither refs nor materialized documents
    assert ctx.version >= 2  # but the history did grow


def test_context_create_idempotent_by_id():

    ctx = Context()
    fake = Question(text="x")
    art1 = ctx.create(fake, id="stable-1")
    events_before = len(ctx._events)
    art2 = ctx.create(fake, id="stable-1")

    assert art1.id == art2.id == "stable-1"
    assert len(ctx._events) == events_before  # the second create did not spawn an event


def test_patch_create_idempotent_by_id():
    class Number(BaseModel):
        value: int

    from reactifact import EventType, Trigger

    class Counter(Agent):
        def __init__(self):
            super().__init__(
                name="counter",
                triggers=[Trigger(EventType.ARTIFACT_CREATED, Question)],
            )

        async def run(self, event, context):
            return Patch().create(Number(value=42), id="the-number")

    ctx = Context()
    runtime = Runtime(ctx, agents=[Counter()])

    ctx.create(Question(text="a"))
    asyncio.run(runtime.arun())
    ctx.create(
        Question(text="b")
    )  # the second question will spawn a Create again, but skip
    asyncio.run(runtime.arun())

    numbers = ctx.list_artifacts(Number)
    assert len(numbers) == 1
    assert numbers[0].id == "the-number"
