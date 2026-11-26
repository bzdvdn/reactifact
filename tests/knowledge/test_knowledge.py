from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from reactifact import Progress, RuntimeResources
from reactifact.eval import RetrievalCase
from reactifact.knowledge import (
    DEFAULT_RERANK_SYSTEM,
    MMR,
    RERANK_OUTPUT_CONTRACT,
    CachingEmbedder,
    Chunk,
    ConfigError,
    DocumentMeta,
    FilesystemConnector,
    HashEmbedder,
    IndexSource,
    IngestProgress,
    IngestReport,
    IngestReportArtifact,
    IngestRequest,
    IngestStep,
    KnowledgeError,
    KnowledgePipeline,
    MarkdownExtractor,
    MemoryIndex,
    PipelineError,
    ProviderEmbedder,
    RawDocument,
    SearchHit,
    SectionsChunker,
    SourceError,
    SqliteDocStore,
    SqliteIndex,
    content_hash,
    document_key,
    evaluate_pipeline,
    llm_reranker,
    query,
    query_by_vector,
    render_rerank_prompt,
    run,
)
from reactifact.knowledge.diversity import mmr_select
from reactifact.providers import (
    FakeEmbedder,
    FakeLLM,
    LLMRequest,
    LLMResponse,
)


def _doc(uri: str, text: str) -> RawDocument:
    return RawDocument.from_text(
        step_id=uri,
        uri=uri,
        text=text,
        title=uri,
        metadata={"documentId": uri, "sourceUrl": f"https://x/{uri}"},
    )


class DictConnector:
    source_id = "test"

    def __init__(self, documents: dict[str, RawDocument]) -> None:
        self.documents = documents
        self.fetch_calls = 0

    async def fetch(self) -> list[RawDocument]:
        self.fetch_calls += 1
        return list(self.documents.values())


class ListingDictConnector(DictConnector):
    async def list_documents(self) -> list[DocumentMeta]:
        return [
            DocumentMeta(
                uri=document.uri,
                step_id=document.step_id,
                fingerprint=content_hash(document),
            )
            for document in self.documents.values()
        ]

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        self.fetch_calls += 1
        values = list(self.documents.values())
        if uris is None:
            return values
        return [document for document in values if document.uri in uris]


class RecordingIndex:
    def __init__(self) -> None:
        self.inner = MemoryIndex()
        self.reconcile_calls = 0

    async def upsert(self, *args: object, **kwargs: object) -> None:
        await self.inner.upsert(*args, **kwargs)

    async def reconcile(self, source_id: str, current: Mapping[str, str]) -> None:
        self.reconcile_calls += 1
        await self.inner.reconcile(source_id, current)

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        return await self.inner.search(
            vector,
            top_k=top_k,
            filters=filters,
            principals=principals,
        )


def test_extract_and_chunk() -> None:
    document = RawDocument.from_text(
        step_id="a",
        uri="a.md",
        text="# Intro\n\nHello\n\n## Details\n\nWorld",
        title="A",
    )
    extracted = asyncio.run(MarkdownExtractor().extract(document))
    assert extracted.title == "Intro"
    assert [section.heading for section in extracted.sections] == [
        "Intro",
        "Details",
    ]
    chunks = asyncio.run(SectionsChunker(max_tokens=10).chunk(extracted))
    assert chunks
    assert chunks[0].metadata["title"] == "Intro"


def test_hash_embedder_cache() -> None:
    cached = CachingEmbedder(HashEmbedder(dimensions=8))
    chunks = [
        Chunk(text="alpha", source_uri="a"),
        Chunk(text="beta", source_uri="b"),
    ]
    first = asyncio.run(cached.embed_batch(chunks))
    second = asyncio.run(cached.embed_batch(chunks))
    assert first == second
    assert cached.cache_hits == 2
    assert cached.cache_misses == 2


def test_provider_embedder_uses_core_provider() -> None:
    embedder = ProviderEmbedder(FakeEmbedder(dim=8), batch_size=2, max_concurrency=2)
    vectors = asyncio.run(
        embedder.embed_batch([Chunk(text=str(i), source_uri=str(i)) for i in range(5)])
    )
    assert len(vectors) == 5
    assert all(len(vector) == 8 for vector in vectors)
    assert embedder.signature.startswith("FakeEmbedder")


def test_memory_index_roundtrip_and_reconcile() -> None:
    index = MemoryIndex()
    asyncio.run(
        index.upsert(
            Chunk(text="refund policy", source_uri="a"),
            [1.0, 0.0],
            source_id="docs",
            artifact_hash="h1",
        )
    )
    asyncio.run(
        index.upsert(
            Chunk(text="install guide", source_uri="b"),
            [0.0, 1.0],
            source_id="docs",
            artifact_hash="h2",
        )
    )
    hits = asyncio.run(index.search([1.0, 0.0], top_k=1))
    assert hits[0].text == "refund policy"
    keyword = asyncio.run(index.keyword_search("install", top_k=1))
    assert keyword[0].text == "install guide"
    asyncio.run(index.reconcile("docs", {"a": "h1"}))
    assert asyncio.run(index.count()) == 1
    assert index.entries()[0][0].source_uri == "a"


def test_sqlite_index_roundtrip(tmp_path: Path) -> None:
    index = SqliteIndex(str(tmp_path) + "/knowledge.db")
    asyncio.run(
        index.upsert(
            Chunk(text="refund policy", source_uri="a"),
            [1.0, 0.0],
            source_id="docs",
            artifact_hash="h1",
        )
    )
    hits = asyncio.run(index.search([1.0, 0.0], top_k=1))
    assert hits[0].text == "refund policy"
    assert asyncio.run(index.count()) == 1
    asyncio.run(index.reconcile("docs", {}))
    assert asyncio.run(index.count()) == 0


def test_pipeline_incremental_and_query(tmp_path: Path) -> None:
    connector = ListingDictConnector(
        {
            "a.md": _doc("a.md", "# Refunds\n\nRefunds are available."),
            "b.md": _doc("b.md", "# Install\n\nRun pip install."),
        }
    )
    index = MemoryIndex()
    pipeline = KnowledgePipeline(
        connector,
        chunker=SectionsChunker(max_tokens=50, overlap=5),
        embedder=HashEmbedder(dimensions=64),
        index=index,
    )
    state = f"{tmp_path}/state.json"
    first = asyncio.run(pipeline.run(incremental=True, state_path=state))
    assert first.documents_ingested == 2
    assert first.documents_changed == 2
    assert asyncio.run(index.count()) > 0

    hits = asyncio.run(pipeline.query("refund", top_k=3))
    assert hits

    calls = connector.fetch_calls
    second = asyncio.run(pipeline.run(incremental=True, state_path=state))
    assert second.documents_skipped == 2
    assert connector.fetch_calls == calls

    connector.documents = {"b.md": _doc("b.md", "# Install\n\nRun uv add reactifact.")}
    third = asyncio.run(pipeline.run(incremental=True, state_path=state))
    assert third.documents_changed == 1
    assert third.documents_deleted == 1


def test_source_error_aborts_before_reconcile(tmp_path: Path) -> None:
    class FailingConnector(ListingDictConnector):
        async def list_documents(self) -> list[DocumentMeta]:
            raise SourceError("source is down")

    index = RecordingIndex()
    pipeline = KnowledgePipeline(
        FailingConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=index,
    )
    with pytest.raises(SourceError):
        asyncio.run(pipeline.run(incremental=True, state_path=f"{tmp_path}/state.json"))
    assert index.reconcile_calls == 0


def test_index_source_bridge() -> None:
    connector = DictConnector(
        {"a.md": _doc("a.md", "# Refund\n\nRefund policy is lenient.")}
    )
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())
    source = IndexSource(pipeline, min_score=0.0)
    refs = asyncio.run(source.asearch("refund", limit=2))
    assert refs
    text = asyncio.run(source.resolve(refs[0]))
    assert "Refund" in text


def test_pipeline_from_resources_uses_core_embedder() -> None:
    resources = RuntimeResources(embedder=FakeEmbedder(dim=8))
    pipeline = KnowledgePipeline.from_resources(
        resources,
        DictConnector({}),
        index=MemoryIndex(),
    )
    assert isinstance(pipeline.embedder, ProviderEmbedder)
    assert isinstance(asyncio.run(pipeline.run()), IngestReport)


class FixedIndex:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        return list(self._hits[:top_k])


def _ten_docs() -> dict[str, RawDocument]:
    return {
        f"d{i}.md": _doc(f"d{i}.md", f"# Doc {i}\n\nrefund policy {i}")
        for i in range(10)
    }


def test_query_rerank_receives_overfetched_candidates() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(_ten_docs()),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())
    seen: dict[str, int] = {}

    def rerank(query: str, hits: Sequence[SearchHit], top_k: int) -> list[SearchHit]:
        seen["count"] = len(hits)
        return list(hits)[:top_k]

    hits = asyncio.run(pipeline.query("refund", top_k=2, rerank=rerank))
    assert len(hits) == 2
    assert seen["count"] == 8


def test_query_rerank_sync_reorders() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(_ten_docs()),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    def rerank(query: str, hits: Sequence[SearchHit], top_k: int) -> list[SearchHit]:
        return sorted(hits, key=lambda hit: hit.source_uri, reverse=True)[:top_k]

    hits = asyncio.run(pipeline.query("refund", top_k=3, rerank=rerank))
    assert [hit.source_uri for hit in hits] == [
        "d9.md",
        "d8.md",
        "d7.md",
    ]


def test_query_rerank_async() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(_ten_docs()),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    async def rerank(
        query: str, hits: Sequence[SearchHit], top_k: int
    ) -> list[SearchHit]:
        return list(reversed(hits))[:top_k]

    hits = asyncio.run(pipeline.query("refund", top_k=2, rerank=rerank))
    assert len(hits) == 2


def test_query_rerank_sees_min_score_filtered_candidates() -> None:
    index = FixedIndex(
        [
            SearchHit(text="keep", source_uri="a", score=0.9),
            SearchHit(text="drop", source_uri="b", score=0.1),
        ]
    )
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=index,
    )
    seen: dict[str, list[str]] = {}

    def rerank(query: str, hits: Sequence[SearchHit], top_k: int) -> list[SearchHit]:
        seen["texts"] = [hit.text for hit in hits]
        return list(hits)

    asyncio.run(pipeline.query("x", top_k=5, min_score=0.5, rerank=rerank))
    assert seen["texts"] == ["keep"]


def test_query_rerank_exception_propagates() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(_ten_docs()),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    def rerank(query: str, hits: Sequence[SearchHit], top_k: int) -> list[SearchHit]:
        raise RuntimeError("domain rerank failed")

    with pytest.raises(RuntimeError, match="domain rerank failed"):
        asyncio.run(pipeline.query("refund", top_k=2, rerank=rerank))


def test_index_source_passes_rerank() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(_ten_docs()),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    def rerank(query: str, hits: Sequence[SearchHit], top_k: int) -> list[SearchHit]:
        return sorted(hits, key=lambda hit: hit.source_uri)[:top_k]

    source = IndexSource(pipeline, rerank=rerank)
    refs = asyncio.run(source.asearch("refund", limit=2))
    assert [ref.locator for ref in refs] == ["d0.md", "d1.md"]


class RecordingLLM(FakeLLM):
    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().complete(request)


def _three_hits() -> list[SearchHit]:
    return [
        SearchHit(text=f"excerpt {i}", source_uri=f"d{i}", score=1.0 - i * 0.1)
        for i in range(3)
    ]


def test_llm_reranker_reorders() -> None:
    rerank = llm_reranker(FakeLLM('{"order": [3, 2, 1]}'))
    hits = asyncio.run(rerank("query", _three_hits(), 3))
    assert [hit.source_uri for hit in hits] == ["d2", "d1", "d0"]


def test_llm_reranker_custom_prompts_and_temperature() -> None:
    llm = RecordingLLM('{"order": [1]}')
    rerank = llm_reranker(
        llm,
        system_prompt="Custom system. " + RERANK_OUTPUT_CONTRACT,
        instructions="Prefer runbooks.",
        user_prompt=lambda query, hits, top_k: f"Q={query};N={len(hits)};K={top_k}",
        temperature=0.2,
    )
    asyncio.run(rerank("query", _three_hits()[:1], 1))

    request = llm.requests[0]
    assert request.messages[0].content.startswith("Custom system.")
    assert "Prefer runbooks." in request.messages[0].content
    assert request.messages[1].content == "Q=query;N=1;K=1"
    assert request.temperature == 0.2
    assert request.response_format == {"type": "json_object"}


def test_llm_reranker_default_prompt_truncates_excerpts() -> None:
    llm = RecordingLLM('{"order": [1]}')
    rerank = llm_reranker(llm, max_excerpt_chars=5)
    asyncio.run(
        rerank(
            "query",
            [SearchHit(text="abcdefgh", source_uri="a", score=1.0)],
            1,
        )
    )
    assert "abcde…" in llm.requests[0].messages[1].content


def test_llm_reranker_fail_soft_on_invalid_json() -> None:
    errors: list[Exception] = []
    rerank = llm_reranker(FakeLLM("not json"), on_error=errors.append)
    hits = asyncio.run(rerank("query", _three_hits(), 2))
    assert [hit.source_uri for hit in hits] == ["d0", "d1"]
    assert errors and isinstance(errors[0], ValueError)


def test_llm_reranker_fail_soft_on_non_permutation() -> None:
    errors: list[Exception] = []
    rerank = llm_reranker(FakeLLM('{"order": [1, 1]}'), on_error=errors.append)
    hits = asyncio.run(rerank("query", _three_hits(), 2))
    assert [hit.source_uri for hit in hits] == ["d0", "d1"]
    assert errors and "permutation" in str(errors[0])


def test_llm_reranker_empty_hits_does_not_call_llm() -> None:
    llm = RecordingLLM('{"order": []}')
    rerank = llm_reranker(llm)
    assert asyncio.run(rerank("query", [], 5)) == []
    assert llm.requests == []


def test_llm_reranker_requires_chat_provider() -> None:
    with pytest.raises(ConfigError):
        llm_reranker(None)  # type: ignore[arg-type]


def test_query_uses_llm_reranker_over_fetched_candidates() -> None:
    index = FixedIndex(
        [
            SearchHit(text="a", source_uri="a", score=0.9),
            SearchHit(text="b", source_uri="b", score=0.8),
            SearchHit(text="c", source_uri="c", score=0.7),
            SearchHit(text="d", source_uri="d", score=0.6),
        ]
    )
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=index,
    )
    rerank = llm_reranker(FakeLLM('{"order": [4, 3, 2, 1]}'))
    hits = asyncio.run(pipeline.query("query", top_k=2, rerank=rerank))
    assert [hit.source_uri for hit in hits] == ["d", "c"]


def test_rerank_prompt_exports_are_usable() -> None:
    prompt = render_rerank_prompt("query", _three_hits(), 3)
    assert DEFAULT_RERANK_SYSTEM.endswith(RERANK_OUTPUT_CONTRACT)
    assert "Query: query" in prompt
    assert "[3]" in prompt


def test_memory_and_sqlite_search_scope_by_source(tmp_path: Path) -> None:
    for index in (MemoryIndex(), SqliteIndex(str(tmp_path / "scope.db"))):
        asyncio.run(
            index.upsert(
                Chunk(text="same text", source_uri="a"),
                [1.0, 0.0],
                source_id="a",
                artifact_hash="h",
            )
        )
        asyncio.run(
            index.upsert(
                Chunk(text="same text", source_uri="b"),
                [1.0, 0.0],
                source_id="b",
                artifact_hash="h",
            )
        )
        dense = asyncio.run(index.search([1.0, 0.0], top_k=5, source_id="a"))
        keyword = asyncio.run(index.keyword_search("same", top_k=5, source_id="b"))
        assert [hit.source_uri for hit in dense] == ["a"]
        assert [hit.source_uri for hit in keyword] == ["b"]


def test_pipeline_query_is_scoped_to_its_source() -> None:
    index = MemoryIndex()
    one = KnowledgePipeline(
        DictConnector({"one.md": _doc("one.md", "# One\n\nrefund policy")}),
        embedder=HashEmbedder(dimensions=32),
        index=index,
        source_id="one",
    )
    two = KnowledgePipeline(
        DictConnector({"two.md": _doc("two.md", "# Two\n\nrefund policy")}),
        embedder=HashEmbedder(dimensions=32),
        index=index,
        source_id="two",
    )
    asyncio.run(one.run())
    asyncio.run(two.run())

    one_hits = asyncio.run(one.query("refund", top_k=5))
    two_hits = asyncio.run(two.query("refund", top_k=5))
    assert {hit.source_uri for hit in one_hits} == {"one.md"}
    assert {hit.source_uri for hit in two_hits} == {"two.md"}


def test_metadata_only_change_is_reindexed(tmp_path: Path) -> None:
    document = _doc("a.md", "# Refund\n\npolicy")
    connector = ListingDictConnector({"a.md": document})
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    state = f"{tmp_path}/state.json"
    asyncio.run(pipeline.run(incremental=True, state_path=state))

    changed = document.model_copy(
        update={"metadata": {**document.metadata, "acl": ["ops"]}}
    )
    connector.documents = {"a.md": changed}
    report = asyncio.run(pipeline.run(incremental=True, state_path=state))
    assert report.documents_changed == 1


def test_query_rejects_min_score_with_hybrid() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=FixedIndex([]),
    )
    with pytest.raises(ConfigError, match="min_score"):
        asyncio.run(pipeline.query("x", hybrid=True, min_score=0.5))


def test_query_rejects_hybrid_without_keyword_search() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=FixedIndex([]),
    )
    with pytest.raises(ConfigError, match="keyword_search"):
        asyncio.run(pipeline.query("x", hybrid=True))


def test_query_can_use_a_bare_index_with_an_embedder() -> None:
    hits = asyncio.run(
        query(
            FixedIndex([SearchHit(text="x", source_uri="a", score=1.0)]),
            "x",
            embedder=HashEmbedder(dimensions=8),
        )
    )
    assert [hit.source_uri for hit in hits] == ["a"]


def test_pipeline_closes_its_connector() -> None:
    class ClosingConnector(DictConnector):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    connector = ClosingConnector({})
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.aclose())
    assert connector.closed is True


def test_ingest_step_returns_a_report_artifact(tmp_path: Path) -> None:
    pipeline = KnowledgePipeline(
        DictConnector({"a.md": _doc("a.md", "# A\n\ntext")}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    artifact = asyncio.run(
        IngestStep(pipeline).run(
            IngestRequest(state_path=f"{tmp_path}/s.json"),
            Progress(lambda *args, **kwargs: None),
        )
    )
    assert isinstance(artifact, IngestReportArtifact)
    assert artifact.source_id == "test"


def test_collect_isolates_a_failed_document_and_retries_it(tmp_path: Path) -> None:
    good = _doc("good.md", "# Good\n\nrefund policy")
    bad = RawDocument.from_text(
        step_id="bad.pdf",
        uri="bad.pdf",
        text="not supported yet",
        content_type="application/pdf",
    )
    connector = ListingDictConnector({"good.md": good, "bad.pdf": bad})
    index = MemoryIndex()
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=index,
    )
    state = f"{tmp_path}/state.json"

    report = asyncio.run(
        pipeline.run(incremental=True, state_path=state, on_error="collect")
    )
    assert report.documents_failed == 1
    assert [failure.uri for failure in report.failures] == ["bad.pdf"]
    assert report.chunks_indexed > 0
    assert {hit.source_uri for hit in asyncio.run(pipeline.query("refund"))} == {
        "good.md"
    }

    # The failed document was not committed, and the old (absent) state is kept;
    # fixing the source makes the next run retry it.
    connector.documents = {
        "good.md": good,
        "bad.pdf": _doc("bad.pdf", "# Fixed\n\nrefund policy"),
    }
    retry = asyncio.run(pipeline.run(incremental=True, state_path=state))
    assert retry.documents_failed == 0
    assert retry.documents_changed == 1
    assert {hit.source_uri for hit in asyncio.run(pipeline.query("refund"))} == {
        "good.md",
        "bad.pdf",
    }


def test_fail_fast_still_raises_on_a_bad_document(tmp_path: Path) -> None:
    bad = RawDocument.from_text(
        step_id="bad.pdf",
        uri="bad.pdf",
        text="not supported yet",
        content_type="application/pdf",
    )
    pipeline = KnowledgePipeline(
        DictConnector({"bad.pdf": bad}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    with pytest.raises(PipelineError, match="bad.pdf"):
        asyncio.run(pipeline.run(on_error="fail_fast"))


def test_ingest_progress_events_cover_the_stages() -> None:
    events: list[IngestProgress] = []
    pipeline = KnowledgePipeline(
        DictConnector({"a.md": _doc("a.md", "# A\n\ntext")}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run(on_progress=events.append))

    stages = [event.stage for event in events]
    assert stages[0] == "start"
    assert stages[-1] == "done"
    assert {"fetch", "extract", "embed", "index", "reconcile", "document"} <= set(
        stages
    )
    assert any(event.uri == "a.md" for event in events)


def test_document_concurrency_extracts_in_parallel() -> None:
    class SlowExtractor:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def extract(self, document: RawDocument):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return await MarkdownExtractor().extract(document)

    extractor = SlowExtractor()
    pipeline = KnowledgePipeline(
        DictConnector(
            {f"d{i}.md": _doc(f"d{i}.md", f"# D{i}\n\ntext {i}") for i in range(4)}
        ),
        extractor=extractor,
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
        document_concurrency=2,
    )
    asyncio.run(pipeline.run())

    assert extractor.peak == 2


def test_invalid_on_error_is_rejected() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    with pytest.raises(ConfigError, match="on_error"):
        asyncio.run(pipeline.run(on_error="oops"))  # type: ignore[arg-type]


def test_collect_without_docstore_keeps_existing_points() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({"a.md": _doc("a.md", "# A\n\nrefund policy")}),
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())
    assert {hit.source_uri for hit in asyncio.run(pipeline.query("refund"))} == {"a.md"}

    broken = RawDocument.from_text(
        step_id="a.md",
        uri="a.md",
        text="unsupported",
        content_type="application/pdf",
    )
    pipeline.connector.documents = {"a.md": broken}
    report = asyncio.run(pipeline.run(on_error="collect"))

    # No DocStore means no previous hash to restore; the run must not treat
    # the failure as a deletion.
    assert report.documents_failed == 1
    assert {hit.source_uri for hit in asyncio.run(pipeline.query("refund"))} == {"a.md"}


def test_sync_run_forwards_the_error_policy() -> None:
    broken = RawDocument.from_text(
        step_id="bad.pdf",
        uri="bad.pdf",
        text="unsupported",
        content_type="application/pdf",
    )
    pipeline = KnowledgePipeline(
        DictConnector({"bad.pdf": broken}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    report = run(pipeline, on_error="collect")
    assert report.documents_failed == 1


def test_concurrent_runs_are_serialized_per_state_path(tmp_path: Path) -> None:
    shared = {"active": 0, "peak": 0}

    class SlowConnector(DictConnector):
        async def fetch(self) -> list[RawDocument]:
            shared["active"] += 1
            shared["peak"] = max(shared["peak"], shared["active"])
            await asyncio.sleep(0.02)
            shared["active"] -= 1
            return await super().fetch()

    state = f"{tmp_path}/state.json"
    one = KnowledgePipeline(
        SlowConnector({"a.md": _doc("a.md", "# A\n\nrefund")}),
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    two = KnowledgePipeline(
        SlowConnector({"b.md": _doc("b.md", "# B\n\nrefund")}),
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )

    async def both() -> None:
        await asyncio.gather(
            one.run(incremental=True, state_path=state),
            two.run(incremental=True, state_path=state),
        )

    asyncio.run(both())
    assert shared["peak"] == 1


def test_query_by_vector_accepts_a_precomputed_vector() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({"a.md": _doc("a.md", "# A\n\nrefund policy")}),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())
    vector = asyncio.run(
        pipeline.embedder.embed(Chunk(text="refund", source_uri="knowledge:query"))
    )

    hits = asyncio.run(pipeline.query_by_vector(vector, top_k=2))
    bare = asyncio.run(
        query_by_vector(pipeline.index, vector, source_id="test", top_k=2)
    )
    assert [hit.source_uri for hit in hits] == ["a.md"]
    assert [hit.source_uri for hit in bare] == ["a.md"]


def test_query_by_vector_declares_what_it_needs_text_for() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    with pytest.raises(ConfigError, match="query_text"):
        asyncio.run(pipeline.query_by_vector([1.0, 0.0], hybrid=True))
    with pytest.raises(ConfigError, match="rerank"):
        asyncio.run(
            pipeline.query_by_vector(
                [1.0, 0.0], rerank=lambda text, hits, top_k: list(hits)
            )
        )


def test_max_per_document_limits_chunks_per_document() -> None:
    documents = {
        "a.md": _doc("a.md", "# One\n\nalpha refund\n\n# Two\n\nbeta refund"),
        "b.md": _doc("b.md", "# Three\n\ngamma refund\n\n# Four\n\ndelta refund"),
    }
    pipeline = KnowledgePipeline(
        DictConnector(documents),
        chunker=SectionsChunker(max_tokens=50, overlap=0),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    unlimited = asyncio.run(pipeline.query("refund", top_k=10))
    assert len(unlimited) == 4
    limited = asyncio.run(pipeline.query("refund", top_k=10, max_per_document=1))
    assert len(limited) == 2
    assert len({hit.source_uri for hit in limited}) == 2

    with pytest.raises(ConfigError, match="max_per_document"):
        asyncio.run(pipeline.query("refund", max_per_document=0))


def test_chunks_carry_citation_coordinates() -> None:
    document = RawDocument.from_text(
        step_id="a",
        uri="a.md",
        text="# Intro\n\nHello\n\n## Details\n\nWorld",
        title="A",
    )
    extracted = asyncio.run(MarkdownExtractor().extract(document))
    chunks = asyncio.run(SectionsChunker(max_tokens=10).chunk(extracted))

    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(
        range(len(chunks))
    )
    assert {chunk.metadata["section_index"] for chunk in chunks} == {0, 1}
    assert chunks[0].metadata["heading"] == "Intro"


def test_pipeline_accepts_a_sqlite_docstore(tmp_path: Path) -> None:
    connector = ListingDictConnector({"a.md": _doc("a.md", "# A\n\nrefund policy")})
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    store = SqliteDocStore(tmp_path / "state.sqlite3")

    first = asyncio.run(pipeline.run(docstore=store))
    second = asyncio.run(pipeline.run(docstore=store))

    assert first.documents_changed == 1
    assert second.documents_skipped == 1
    assert store.last_run("test") is not None
    assert store.last_run("test").ok is True


def test_indexes_reject_dimension_mismatches(tmp_path: Path) -> None:
    for index in (MemoryIndex(), SqliteIndex(str(tmp_path / "dims.db"))):
        asyncio.run(
            index.upsert(
                Chunk(text="a", source_uri="a"),
                [1.0, 0.0],
                source_id="s",
                artifact_hash="h",
            )
        )
        with pytest.raises(ConfigError, match="2-dimensional"):
            asyncio.run(
                index.upsert(
                    Chunk(text="b", source_uri="b"),
                    [1.0, 0.0, 0.0],
                    source_id="s",
                    artifact_hash="h",
                )
            )
        with pytest.raises(ConfigError, match="2-dimensional"):
            asyncio.run(index.search([1.0, 0.0, 0.0]))
        with pytest.raises(ConfigError, match="must not be empty"):
            asyncio.run(index.search([]))


def test_pipeline_rejects_an_embedder_dimension_change() -> None:
    connector = DictConnector({"a.md": _doc("a.md", "# A\n\nrefund")})
    index = MemoryIndex()
    asyncio.run(
        KnowledgePipeline(
            connector,
            embedder=HashEmbedder(dimensions=8),
            index=index,
        ).run()
    )

    changed = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=index,
    )
    with pytest.raises(ConfigError, match="8-dimensional"):
        asyncio.run(changed.run())


def test_query_reports_retrieval_diagnostics() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "a.md": _doc("a.md", "# A\n\nrefund policy"),
                "b.md": _doc("b.md", "# B\n\ninstall guide"),
            }
        ),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    dense = asyncio.run(pipeline.query("refund", top_k=2))
    assert [hit.dense_rank for hit in dense] == [1, 2]
    assert all(hit.score_kind == "dense" for hit in dense)
    assert all(hit.keyword_rank is None for hit in dense)

    hybrid = asyncio.run(pipeline.query("refund", top_k=2, hybrid=True))
    assert hybrid
    assert all(hit.score_kind == "rrf" for hit in hybrid)
    assert all(hit.dense_rank is not None for hit in hybrid)
    assert all(hit.keyword_rank is not None for hit in hybrid)


def test_rerank_diagnostics_keep_parent_ranks() -> None:
    documents = {
        f"d{i}.md": _doc(f"d{i}.md", f"# D{i}\n\nrefund policy {i}") for i in range(4)
    }
    pipeline = KnowledgePipeline(
        DictConnector(documents),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    def reverse(text, hits, top_k):
        return list(reversed(hits))

    base = asyncio.run(pipeline.query("refund", top_k=4))
    reranked = asyncio.run(pipeline.query("refund", top_k=4, rerank=reverse))

    assert [hit.rerank_rank for hit in reranked] == [1, 2, 3, 4]
    assert [hit.dense_rank for hit in reranked] == [
        hit.dense_rank for hit in reversed(base)
    ]


def test_duplicate_chunk_text_gets_distinct_point_ids() -> None:
    index = MemoryIndex()
    for chunk_index in (0, 1):
        asyncio.run(
            index.upsert(
                Chunk(
                    text="same body",
                    source_uri="a.md",
                    metadata={"chunk_index": chunk_index},
                ),
                [1.0, 0.0],
                source_id="s",
                artifact_hash="h",
            )
        )

    assert asyncio.run(index.count()) == 2
    hits = asyncio.run(index.search([1.0, 0.0], top_k=5))
    assert len(hits) == 2
    assert len({hit.chunk_id for hit in hits}) == 2


def test_index_source_exposes_chunk_fragments_and_bounds_its_cache() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "a.md": _doc("a.md", "# A\n\nrefund policy"),
                "b.md": _doc("b.md", "# B\n\nrefund policy"),
                "c.md": _doc("c.md", "# C\n\nrefund policy"),
            }
        ),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())
    source = IndexSource(pipeline, max_hits=2)

    refs = asyncio.run(source.asearch("refund", limit=3))
    assert len(refs) == 3
    assert len({ref.fragment for ref in refs}) == 3
    assert len(source._hits) == 2
    assert all(ref.metadata["retrieval"]["score_kind"] == "dense" for ref in refs)


def test_evaluate_pipeline_scores_retrieval() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "a.md": _doc("a.md", "# Refund\n\nrefund policy"),
                "b.md": _doc("b.md", "# Install\n\ninstall guide"),
            }
        ),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    report = asyncio.run(
        evaluate_pipeline(
            pipeline,
            [
                RetrievalCase(query="refund", relevant=["a.md"]),
                RetrievalCase(query="install", relevant=["b.md"]),
            ],
            k=1,
        )
    )

    assert report.aggregate()["recall"] == 1.0
    assert report.aggregate()["mrr"] == 1.0
    assert report.retrieved["refund"] == ["a.md"]
    assert document_key(SearchHit(text="x", source_uri="a.md", score=1.0)) == "a.md"


class SemanticEmbedder:
    """Two-axis embedder: refund docs vs everything else."""

    dimensions = 2

    def _vector(self, text: str) -> list[float]:
        return [1.0, 0.0] if "refund" in text else [0.0, 1.0]

    async def embed(self, chunk: Chunk) -> list[float]:
        return self._vector(chunk.text)

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        return [self._vector(chunk.text) for chunk in chunks]


def test_mmr_select_balances_relevance_and_novelty() -> None:
    hits = [
        SearchHit(text=f"h{index}", source_uri="a", score=1.0) for index in range(3)
    ]
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    diverse = mmr_select([1.0, 0.0], hits, vectors, limit=2, lambda_=0.3)
    relevant = mmr_select([1.0, 0.0], hits, vectors, limit=2, lambda_=1.0)

    assert [hit.text for hit in diverse] == ["h0", "h2"]
    assert [hit.text for hit in relevant] == ["h0", "h1"]


def test_mmr_validates_lambda() -> None:
    with pytest.raises(ConfigError, match="lambda_"):
        MMR(lambda_=1.5)


def test_query_mmr_surfaces_a_distinct_document() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "a.md": _doc("a.md", "# A\n\nrefund policy"),
                "b.md": _doc("b.md", "# B\n\nrefund rules"),
                "c.md": _doc("c.md", "# C\n\ninstall guide"),
            }
        ),
        embedder=SemanticEmbedder(),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    plain = asyncio.run(pipeline.query("refund", top_k=2))
    diverse = asyncio.run(pipeline.query("refund", top_k=2, mmr=MMR(lambda_=0.3)))

    assert {hit.source_uri for hit in plain} == {"a.md", "b.md"}
    assert {hit.source_uri for hit in diverse} == {"a.md", "c.md"}


def test_query_by_vector_mmr_requires_an_embedder() -> None:
    pipeline = KnowledgePipeline(
        DictConnector({"a.md": _doc("a.md", "# A\n\nrefund")}),
        embedder=HashEmbedder(dimensions=8),
        index=MemoryIndex(),
    )
    with pytest.raises(ConfigError, match="embedder"):
        asyncio.run(
            query_by_vector(
                pipeline,
                [1.0, 0.0],
                mmr=MMR(lambda_=0.3),
            )
        )


def _write_guide(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Intro\n\nfirst paragraph\n\n## Details\n\nsecond paragraph\n",
        encoding="utf-8",
    )
    return docs


def test_index_source_expands_sections_and_documents(tmp_path: Path) -> None:
    pipeline = KnowledgePipeline(
        FilesystemConnector(_write_guide(tmp_path), source_id="docs"),
        chunker=SectionsChunker(max_tokens=100, overlap=0),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    section_source = IndexSource(pipeline, expand="section")
    refs = asyncio.run(section_source.asearch("Details", limit=5))
    details = next(ref for ref in refs if ref.metadata.get("heading") == "Details")
    text = asyncio.run(section_source.resolve(details))
    assert "second paragraph" in text
    assert "first paragraph" not in text

    document_source = IndexSource(pipeline, expand="document")
    document_refs = asyncio.run(document_source.asearch("Details", limit=5))
    full = asyncio.run(document_source.resolve(document_refs[0]))
    assert "first paragraph" in full
    assert "second paragraph" in full


def test_index_source_expands_with_a_plain_connector() -> None:
    pipeline = KnowledgePipeline(
        DictConnector(
            {"a.md": _doc("a.md", "# Intro\n\nfirst\n\n## Details\n\nsecond")}
        ),
        chunker=SectionsChunker(max_tokens=100, overlap=0),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    source = IndexSource(pipeline, expand="section")
    refs = asyncio.run(source.asearch("Details", limit=5))
    details = next(ref for ref in refs if ref.metadata.get("heading") == "Details")
    assert "second" in asyncio.run(source.resolve(details))


def test_index_source_expansion_fails_explicitly(tmp_path: Path) -> None:
    docs = _write_guide(tmp_path)
    pipeline = KnowledgePipeline(
        FilesystemConnector(docs, source_id="docs"),
        chunker=SectionsChunker(max_tokens=100, overlap=0),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    source = IndexSource(pipeline, expand="section")
    refs = asyncio.run(source.asearch("Details", limit=5))
    missing = refs[0].model_copy(
        update={
            "fragment": "missing",
            "metadata": {
                **refs[0].metadata,
                "section_index": 99,
                "heading": "",
            },
        }
    )
    with pytest.raises(KnowledgeError, match="section"):
        asyncio.run(source.resolve(missing))

    (docs / "guide.md").unlink()
    with pytest.raises(KnowledgeError, match="did not return"):
        asyncio.run(IndexSource(pipeline, expand="section").resolve(refs[0]))

    with pytest.raises(ConfigError, match="expand"):
        IndexSource(pipeline, expand="page")  # type: ignore[arg-type]


def test_index_source_applies_principals_and_require_principals() -> None:
    def acl_doc(uri: str, acl: list[str] | None) -> RawDocument:
        metadata = {"documentId": uri}
        if acl is not None:
            metadata["acl"] = acl
        return _doc(uri, "# A\n\nrefund policy").model_copy(
            update={"metadata": metadata}
        )

    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "public.md": acl_doc("public.md", None),
                "ops.md": acl_doc("ops.md", ["ops"]),
            }
        ),
        embedder=HashEmbedder(dimensions=32),
        index=MemoryIndex(),
    )
    asyncio.run(pipeline.run())

    public = asyncio.run(IndexSource(pipeline, principals=["eng"]).asearch("refund"))
    assert {ref.locator for ref in public} == {"public.md"}

    ops = asyncio.run(IndexSource(pipeline, principals=["ops"]).asearch("refund"))
    assert {ref.locator for ref in ops} == {"public.md", "ops.md"}

    dynamic = asyncio.run(
        IndexSource(pipeline, principals=lambda: ["ops"]).asearch("refund")
    )
    assert {ref.locator for ref in dynamic} == {"public.md", "ops.md"}

    with pytest.raises(ConfigError, match="principals"):
        asyncio.run(IndexSource(pipeline, require_principals=True).asearch("refund"))
