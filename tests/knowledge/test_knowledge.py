from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from reactifact import RuntimeResources
from reactifact.knowledge import (
    DEFAULT_RERANK_SYSTEM,
    RERANK_OUTPUT_CONTRACT,
    CachingEmbedder,
    Chunk,
    ConfigError,
    DocumentMeta,
    HashEmbedder,
    IndexSource,
    IngestReport,
    KnowledgePipeline,
    MarkdownExtractor,
    MemoryIndex,
    ProviderEmbedder,
    RawDocument,
    SearchHit,
    SectionsChunker,
    SourceError,
    SqliteIndex,
    content_hash,
    llm_reranker,
    render_rerank_prompt,
)
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
    assert index.count == 1
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
    assert index.count > 0

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
