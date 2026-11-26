"""Code-first knowledge pipeline: connector -> extract -> chunk -> embed -> index.

There is deliberately no YAML loader, CLI, plugin registry or type-string
factory. A pipeline is an ordinary object built from collaborators:

    pipeline = KnowledgePipeline(
        connector,
        chunker=SectionsChunker(max_tokens=400, overlap=60),
        embedder=ProviderEmbedder(resources.embedder),
        index=QdrantIndex(url, "knowledge"),
    )
    report = await pipeline.run(incremental=True, state_path="state.json")
    hits = await query(pipeline, "how do I install it?", top_k=5, hybrid=True)

The execution model is fixed and linear — this is ingestion, not agent
orchestration. `reactifact.knowledge.bridge` exposes the optional adapters
into `reactifact.Source` / `reactifact.Step`.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from ..providers import EmbeddingProvider
from ._closers import aclose_of
from .chunk import AutoChunker, Chunker
from .connectors import Connector, ListingConnector, has_listing
from .diversity import MMR, mmr_select
from .docstore import DocEntry, DocStore, JsonDocStore, RunRecord
from .embed import Embedder, ProviderEmbedder
from .errors import ConfigError, PipelineError, SourceError
from .extract import AutoExtractor, Extractor
from .indexes import (
    BatchIndexer,
    Indexer,
    KeywordIndexer,
    reciprocal_rank_fusion,
)
from .models import Chunk, DocumentMeta, RawDocument, SearchHit, content_hash
from .rerank import RerankFn, apply_rerank

_CANDIDATE_FACTOR = 4

_RUN_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _run_lock(pipeline: KnowledgePipeline, docstore: DocStore | None) -> asyncio.Lock:
    """Serialize ingestion per `(state_path, source_id)` within the process.

    Cross-process runs still need an external lock; the contract is one writer
    per source at a time.
    """
    if docstore is None:
        return pipeline._run_lock
    key = f"{docstore.path.resolve()}::{pipeline.source_id}"
    lock = _RUN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[key] = lock
    return lock


@dataclass(frozen=True)
class IngestFailure:
    """One document that could not be extracted/indexed."""

    uri: str
    stage: str
    error: str


@dataclass(frozen=True)
class IngestProgress:
    """A progress event emitted by `KnowledgePipeline.run(on_progress=...)`."""

    source_id: str
    stage: str
    uri: str | None = None
    documents: int = 0
    chunks: int = 0
    failed: int = 0


@dataclass
class IngestReport:
    """Summary of one knowledge pipeline run."""

    documents_ingested: int
    chunks_indexed: int
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0
    documents_changed: int = 0
    documents_skipped: int = 0
    documents_deleted: int = 0
    documents_failed: int = 0
    failures: list[IngestFailure] = field(default_factory=list)


PipelineResult = IngestReport
"""Backward-compatible name for code migrating from alvis."""


@dataclass
class _RunState:
    source_id: str
    signature: str
    connector: Connector
    extractor: Extractor
    chunker: Chunker
    embedder: Embedder
    index: Indexer | None
    docstore: DocStore | None
    acl: list[str] | None
    on_error: str = "fail_fast"
    on_progress: Callable[[IngestProgress], None] | None = None
    document_concurrency: int = 1
    use_listing: bool = False
    uri_by_meta: dict[str, DocumentMeta] = field(default_factory=dict)
    artifacts: list[RawDocument] = field(default_factory=list)
    current: dict[str, str] = field(default_factory=dict)
    entries: dict[str, DocEntry] = field(default_factory=dict)
    pending: list[tuple[Chunk, str]] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    documents: int = 0
    indexed: int = 0
    changed: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    failures: list[IngestFailure] = field(default_factory=list)

    @property
    def embed_cache_hits(self) -> int:
        return int(getattr(self.embedder, "cache_hits", 0))

    @property
    def embed_cache_misses(self) -> int:
        return int(getattr(self.embedder, "cache_misses", 0))


class KnowledgePipeline:
    """A linear ingestion pipeline built from explicit collaborators."""

    def __init__(
        self,
        connector: Connector,
        *,
        embedder: Embedder,
        extractor: Extractor | None = None,
        chunker: Chunker | None = None,
        index: Indexer | None = None,
        source_id: str | None = None,
        acl: Sequence[str] | None = None,
        document_concurrency: int = 1,
    ) -> None:
        self.connector = connector
        resolved_source_id = source_id or getattr(connector, "source_id", None)
        if not resolved_source_id:
            raise ConfigError(
                "connector must expose a non-empty `source_id`, or pass "
                "source_id= to KnowledgePipeline"
            )
        self.source_id = str(resolved_source_id)
        self.extractor = extractor or AutoExtractor()
        self.chunker = chunker or AutoChunker()
        self.embedder = embedder
        self.index = index
        self.acl = list(acl) if acl else None
        if document_concurrency < 1:
            raise ConfigError("document_concurrency must be >= 1")
        self.document_concurrency = document_concurrency
        self._run_lock = asyncio.Lock()

    @classmethod
    def from_resources(
        cls,
        resources: Any,
        connector: Connector,
        **kwargs: Any,
    ) -> KnowledgePipeline:
        """Build from a core `RuntimeResources`, reusing its embedder.

        Keeps one provider/settings surface for the whole application instead
        of a second knowledge-specific configuration layer.
        """
        if "embedder" in kwargs:
            return cls(connector, **kwargs)
        embedder: EmbeddingProvider | None = getattr(resources, "embedder", None)
        if embedder is None:
            raise ConfigError(
                "RuntimeResources.embedder is not configured; pass "
                "embedder= or wire one into RuntimeResources"
            )
        kwargs["embedder"] = ProviderEmbedder(embedder)
        return cls(connector, **kwargs)

    def signature(self) -> str:
        """Fingerprint of transformation settings (not content)."""
        payload = {
            "source_id": self.source_id,
            "extractor": _signature(self.extractor),
            "chunker": _signature(self.chunker),
            "embedder": _signature(self.embedder),
            "index": _signature(self.index),
            "acl": self.acl,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    async def run(
        self,
        *,
        incremental: bool = False,
        state_path: str | Path | None = None,
        docstore: DocStore | None = None,
        on_error: Literal["fail_fast", "collect"] = "fail_fast",
        on_progress: Callable[[IngestProgress], None] | None = None,
    ) -> IngestReport:
        """Run the pipeline once.

        With `incremental=True` (or an explicit `docstore`), unchanged
        documents are skipped. A connector failure aborts the run *before*
        reconciliation, so a transient outage never makes the corpus look
        deleted.

        `on_error="collect"` keeps a failed document out of the committed
        state and continues with the rest; the failures are returned in
        `IngestReport.failures`. `"fail_fast"` (default) preserves the old
        abort-on-first-error behavior. `on_progress` receives `IngestProgress`
        events for long ingestion jobs.
        """
        if on_error not in {"fail_fast", "collect"}:
            raise ConfigError("on_error must be 'fail_fast' or 'collect'")
        store = docstore
        if store is None and incremental:
            path = Path(state_path) if state_path else JsonDocStore.default_path()
            store = JsonDocStore(path)
        async with _run_lock(self, store):
            started = time.monotonic()
            signature = self.signature()
            try:
                report = await self._execute(store, signature, on_error, on_progress)
            except Exception as exc:
                if store is not None:
                    store.record_run(
                        self.source_id,
                        RunRecord(
                            at=_now_iso(),
                            ok=False,
                            error=str(exc),
                            duration_seconds=time.monotonic() - started,
                        ),
                    )
                raise
            if store is not None:
                store.record_run(
                    self.source_id,
                    RunRecord(
                        at=_now_iso(),
                        ok=True,
                        duration_seconds=time.monotonic() - started,
                        documents=report.documents_ingested,
                        chunks=report.chunks_indexed,
                        changed=report.documents_changed,
                        skipped=report.documents_skipped,
                        deleted=report.documents_deleted,
                        embed_cache_hits=report.embed_cache_hits,
                        embed_cache_misses=report.embed_cache_misses,
                    ),
                )
            return report

    async def query(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        hybrid: bool = False,
        principals: Sequence[str] | None = None,
        min_score: float | None = None,
        rerank: RerankFn | None = None,
        max_per_document: int | None = None,
        mmr: MMR | None = None,
        embedder: Embedder | None = None,
    ) -> list[SearchHit]:
        """Retrieve the closest chunks to `text`, optionally reranked."""
        return await query(
            self,
            text,
            top_k=top_k,
            filters=filters,
            hybrid=hybrid,
            principals=principals,
            min_score=min_score,
            rerank=rerank,
            max_per_document=max_per_document,
            mmr=mmr,
            embedder=embedder,
        )

    async def query_by_vector(
        self,
        vector: Sequence[float],
        *,
        query_text: str | None = None,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        hybrid: bool = False,
        principals: Sequence[str] | None = None,
        min_score: float | None = None,
        rerank: RerankFn | None = None,
        max_per_document: int | None = None,
        mmr: MMR | None = None,
        embedder: Embedder | None = None,
    ) -> list[SearchHit]:
        """Retrieve the closest chunks to an already computed vector."""
        return await query_by_vector(
            self,
            vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            hybrid=hybrid,
            principals=principals,
            min_score=min_score,
            rerank=rerank,
            max_per_document=max_per_document,
            mmr=mmr,
            embedder=embedder or self.embedder,
        )

    async def aclose(self) -> None:
        """Close owned HTTP clients, if the collaborators expose `aclose`."""
        for collaborator in (self.connector, self.index, self.embedder):
            closer = aclose_of(collaborator)
            if closer is not None:
                await closer()

    async def _execute(
        self,
        docstore: DocStore | None,
        signature: str,
        on_error: str,
        on_progress: Callable[[IngestProgress], None] | None,
    ) -> IngestReport:
        state = _RunState(
            source_id=self.source_id,
            signature=signature,
            connector=self.connector,
            extractor=self.extractor,
            chunker=self.chunker,
            embedder=self.embedder,
            index=self.index,
            docstore=docstore,
            acl=self.acl,
            on_error=on_error,
            on_progress=on_progress,
            document_concurrency=self.document_concurrency,
        )
        _progress(state, "start")
        await _fetch(state)
        _progress(state, "fetch")
        await _extract(state)
        _progress(state, "extract")
        await _embed(state)
        _progress(state, "embed")
        await _upsert(state)
        _progress(state, "index")
        await _reconcile(state)
        _progress(state, "reconcile")
        _commit(state)
        _progress(state, "done")
        return IngestReport(
            documents_ingested=state.documents,
            chunks_indexed=state.indexed,
            embed_cache_hits=state.embed_cache_hits,
            embed_cache_misses=state.embed_cache_misses,
            documents_changed=state.changed,
            documents_skipped=state.skipped,
            documents_deleted=state.deleted,
            documents_failed=state.failed,
            failures=list(state.failures),
        )


def _progress(state: _RunState, stage: str, uri: str | None = None) -> None:
    if state.on_progress is None:
        return
    state.on_progress(
        IngestProgress(
            source_id=state.source_id,
            stage=stage,
            uri=uri,
            documents=state.documents,
            chunks=state.indexed,
            failed=state.failed,
        )
    )


async def _fetch(state: _RunState) -> None:
    connector = state.connector
    try:
        if state.docstore is not None and state.signature and has_listing(connector):
            state.use_listing = True
            listing = cast(ListingConnector, connector)
            metas = await listing.list_documents()
            state.uri_by_meta = {meta.uri: meta for meta in metas}
            wanted: set[str] = set()
            for meta in metas:
                state.documents += 1
                stored = state.docstore.entry(
                    state.source_id, state.signature, meta.uri
                )
                if (
                    stored is not None
                    and stored.listing is not None
                    and stored.listing == meta.fingerprint
                ):
                    state.skipped += 1
                    state.current[meta.uri] = stored.content
                    state.entries[meta.uri] = stored
                else:
                    state.changed += 1
                    wanted.add(meta.uri)
            state.artifacts = await listing.fetch(uris=wanted) if wanted else []
        else:
            state.artifacts = await connector.fetch()
    except SourceError:
        raise
    except Exception as exc:
        raise PipelineError(f"connector '{state.source_id}' failed: {exc}") from exc

    if state.acl:
        state.artifacts = [
            artifact
            if "acl" in artifact.metadata
            else artifact.model_copy(
                update={"metadata": {**artifact.metadata, "acl": state.acl}}
            )
            for artifact in state.artifacts
        ]


async def _extract(state: _RunState) -> None:
    jobs: list[tuple[RawDocument, str, DocumentMeta | None]] = []
    for artifact in state.artifacts:
        artifact_hash = content_hash(artifact)
        if not state.use_listing:
            state.documents += 1
            stored = (
                state.docstore.entry(state.source_id, state.signature, artifact.uri)
                if state.docstore is not None
                else None
            )
            if stored is not None and stored.content == artifact_hash:
                state.skipped += 1
                state.current[artifact.uri] = artifact_hash
                state.entries[artifact.uri] = DocEntry(
                    content=artifact_hash, listing=stored.listing
                )
                continue
            state.changed += 1
        listed = state.uri_by_meta.get(artifact.uri)
        jobs.append((artifact, artifact_hash, listed))

    semaphore = asyncio.Semaphore(max(1, state.document_concurrency))

    async def _one(
        job: tuple[RawDocument, str, DocumentMeta | None],
    ) -> tuple[
        tuple[RawDocument, str, DocumentMeta | None],
        list[Chunk] | None,
        Exception | None,
    ]:
        artifact, _, _ = job
        async with semaphore:
            try:
                document = await state.extractor.extract(artifact)
                chunks = await state.chunker.chunk(document)
            except Exception as exc:  # noqa: BLE001 — per-document isolation
                return job, None, exc
        return job, chunks, None

    def _accept(
        job: tuple[RawDocument, str, DocumentMeta | None],
        chunks: list[Chunk] | None,
        error: Exception | None,
    ) -> None:
        artifact, artifact_hash, listed = job
        if error is not None:
            if state.on_error == "fail_fast":
                raise PipelineError(
                    f"failed on artifact {artifact.uri}: {error}"
                ) from error
            state.failed += 1
            state.failures.append(
                IngestFailure(uri=artifact.uri, stage="extract", error=str(error))
            )
            # Keep the previous version's points/state: a transient extract
            # failure must not look like a deleted document, and the next
            # incremental run must retry it.
            previous = (
                state.docstore.entry(state.source_id, state.signature, artifact.uri)
                if state.docstore is not None
                else None
            )
            if previous is not None:
                state.current[artifact.uri] = previous.content
                state.entries[artifact.uri] = previous
            else:
                state.current.pop(artifact.uri, None)
            _progress(state, "failed", artifact.uri)
            return

        state.current[artifact.uri] = artifact_hash
        state.entries[artifact.uri] = DocEntry(
            content=artifact_hash,
            listing=listed.fingerprint if listed is not None else None,
        )
        for chunk in chunks or ():
            state.indexed += 1
            if state.index is not None:
                enriched = chunk.model_copy(
                    update={
                        "metadata": {
                            **artifact.metadata,
                            **chunk.metadata,
                        }
                    }
                )
                state.pending.append((enriched, artifact_hash))
        _progress(state, "document", artifact.uri)

    tasks = {asyncio.create_task(_one(job)): job for job in jobs}
    try:
        for completed in asyncio.as_completed(tasks):
            job, chunks, error = await completed
            _accept(job, chunks, error)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _embed(state: _RunState) -> None:
    if state.index is None or not state.pending:
        return
    chunks = [chunk for chunk, _ in state.pending]
    try:
        state.vectors = await state.embedder.embed_batch(chunks)
    except (SourceError, ValueError) as exc:
        raise PipelineError(f"embedding failed: {exc}") from exc


async def _upsert(state: _RunState) -> None:
    if state.index is None or not state.pending:
        return
    items = [
        (chunk, vector, artifact_hash)
        for (chunk, artifact_hash), vector in zip(
            state.pending, state.vectors, strict=True
        )
    ]
    if isinstance(state.index, BatchIndexer):
        await state.index.upsert_batch(items, source_id=state.source_id)
        return
    for chunk, vector, artifact_hash in items:
        await state.index.upsert(
            chunk,
            vector,
            source_id=state.source_id,
            artifact_hash=artifact_hash,
        )


async def _reconcile(state: _RunState) -> None:
    if state.index is None:
        return
    if state.failed and state.docstore is None:
        # Without a DocStore there is no previous hash to keep failed
        # documents in `current`, so deletion would look like a corpus change
        # caused by a transient failure. Upserts already ran; skip deletions.
        return
    await state.index.reconcile(state.source_id, state.current)


def _commit(state: _RunState) -> None:
    if state.docstore is not None:
        state.deleted = state.docstore.commit(
            state.source_id, state.signature, state.entries
        )
        state.docstore.save()


async def query(
    pipeline: KnowledgePipeline | Indexer,
    text: str,
    *,
    top_k: int = 5,
    filters: Mapping[str, str] | None = None,
    hybrid: bool = False,
    principals: Sequence[str] | None = None,
    min_score: float | None = None,
    rerank: RerankFn | None = None,
    max_per_document: int | None = None,
    mmr: MMR | None = None,
    source_id: str | None = None,
    embedder: Embedder | None = None,
) -> list[SearchHit]:
    """Retrieve chunks by text, using the pipeline's embedder and index.

    A `KnowledgePipeline` scopes retrieval to its own `source_id`; pass
    `source_id=` to target another source in the same index, or pass a bare
    `Indexer` plus `embedder=` to query it directly.

    `min_score` filters dense cosine results only. Hybrid scores are
    rank-fusion scores (much smaller, not similarities), so combining it with
    `hybrid=True` raises `ConfigError` instead of silently dropping results.

    `max_per_document` caps how many chunks of the same document reach the
    result (documents are identified by `document_id`/`documentId` metadata,
    falling back to `source_uri`). `mmr` re-ranks over-fetched candidates by
    relevance *and* novelty, reusing the pipeline's embedder; it runs after
    `min_score`/`max_per_document` and before `rerank`. `rerank` runs last,
    receives candidates (``top_k * 4``) and the final `top_k`, and may reorder
    or filter them. It is synchronous or asynchronous. Exceptions propagate:
    this is application code, and silently falling back to the original order
    would make a relevance regression invisible. Callers that need best-effort
    retrieval can wrap their reranker in a `try/except`.
    """
    if not text.strip():
        raise ConfigError("query text must not be empty")
    _validate_query(top_k, min_score, hybrid, max_per_document)
    index, resolved_source_id = _resolve_query_target(pipeline, source_id)
    resolved_embedder = embedder
    if isinstance(pipeline, KnowledgePipeline):
        resolved_embedder = embedder or pipeline.embedder
    if resolved_embedder is None:
        raise ConfigError(
            "query needs an embedder; pass embedder= when querying a bare index"
        )
    vector = await resolved_embedder.embed(
        Chunk(text=text, source_uri="knowledge:query")
    )
    return await _retrieve(
        index,
        vector,
        text=text,
        top_k=top_k,
        filters=filters,
        hybrid=hybrid,
        principals=principals,
        min_score=min_score,
        rerank=rerank,
        max_per_document=max_per_document,
        mmr=mmr,
        source_id=resolved_source_id,
        embedder=resolved_embedder,
    )


async def query_by_vector(
    pipeline: KnowledgePipeline | Indexer,
    vector: Sequence[float],
    *,
    query_text: str | None = None,
    top_k: int = 5,
    filters: Mapping[str, str] | None = None,
    hybrid: bool = False,
    principals: Sequence[str] | None = None,
    min_score: float | None = None,
    rerank: RerankFn | None = None,
    max_per_document: int | None = None,
    mmr: MMR | None = None,
    source_id: str | None = None,
    embedder: Embedder | None = None,
) -> list[SearchHit]:
    """Retrieve chunks for an already computed query vector.

    Use this when the embedding comes from elsewhere (a cache, another model,
    or a caller that wants the same vector for several indexes). `query_text`
    is required for `hybrid=True` (BM25 needs the words) and for `rerank=`
    (the reranker needs the query); `embedder=` is required for `mmr`, which
    embeds the candidate texts to measure diversity.
    """
    if not vector:
        raise ConfigError("query vector must not be empty")
    _validate_query(top_k, min_score, hybrid, max_per_document)
    if hybrid and not query_text:
        raise ConfigError(
            "hybrid=True with query_by_vector requires query_text for BM25"
        )
    if rerank is not None and not query_text:
        raise ConfigError("rerank with query_by_vector requires query_text")
    if mmr is not None and embedder is None:
        raise ConfigError("mmr with query_by_vector requires embedder=")
    index, resolved_source_id = _resolve_query_target(pipeline, source_id)
    return await _retrieve(
        index,
        vector,
        text=query_text or "",
        top_k=top_k,
        filters=filters,
        hybrid=hybrid,
        principals=principals,
        min_score=min_score,
        rerank=rerank,
        max_per_document=max_per_document,
        mmr=mmr,
        source_id=resolved_source_id,
        embedder=embedder,
    )


def _resolve_query_target(
    pipeline: KnowledgePipeline | Indexer,
    source_id: str | None,
) -> tuple[Indexer, str | None]:
    if isinstance(pipeline, KnowledgePipeline):
        if pipeline.index is None:
            raise ConfigError("query requires an index")
        return pipeline.index, (pipeline.source_id if source_id is None else source_id)
    return pipeline, source_id


def _validate_query(
    top_k: int,
    min_score: float | None,
    hybrid: bool,
    max_per_document: int | None,
) -> None:
    if top_k < 1:
        raise ConfigError("top_k must be >= 1")
    if max_per_document is not None and max_per_document < 1:
        raise ConfigError("max_per_document must be >= 1")
    if min_score is not None and hybrid:
        raise ConfigError(
            "min_score is a dense-cosine threshold and cannot be combined "
            "with hybrid=True (RRF scores are ranks, not similarities)"
        )


def _document_key(hit: SearchHit) -> str:
    for name in ("document_id", "documentId"):
        value = hit.metadata.get(name)
        if value:
            return str(value)
    return hit.source_uri


def _limit_per_document(
    hits: Sequence[SearchHit],
    limit: int,
) -> list[SearchHit]:
    counts: dict[str, int] = {}
    kept: list[SearchHit] = []
    for hit in hits:
        key = _document_key(hit)
        count = counts.get(key, 0)
        if count >= limit:
            continue
        counts[key] = count + 1
        kept.append(hit)
    return kept


def _ranked(
    hits: Sequence[SearchHit],
    field: str,
) -> list[SearchHit]:
    """Annotate a backend's ranked list with 1-based positions."""
    return [hit.model_copy(update={field: rank}) for rank, hit in enumerate(hits, 1)]


async def _retrieve(
    index: Indexer,
    vector: Sequence[float],
    *,
    text: str,
    top_k: int,
    filters: Mapping[str, str] | None,
    hybrid: bool,
    principals: Sequence[str] | None,
    min_score: float | None,
    rerank: RerankFn | None,
    max_per_document: int | None,
    mmr: MMR | None,
    source_id: str | None,
    embedder: Embedder | None,
) -> list[SearchHit]:
    over_fetch = (
        hybrid or rerank is not None or max_per_document is not None or mmr is not None
    )
    candidate_k = top_k * _CANDIDATE_FACTOR if over_fetch else top_k
    search_kwargs: dict[str, Any] = {
        "top_k": candidate_k,
        "filters": filters,
        "principals": principals,
    }
    if _accepts(index.search, "source_id"):
        search_kwargs["source_id"] = source_id
    dense = _ranked(await index.search(list(vector), **search_kwargs), "dense_rank")
    if not hybrid:
        hits = dense
    else:
        if not isinstance(index, KeywordIndexer):
            raise ConfigError(
                "hybrid=True requires an index with keyword_search; "
                "use hybrid=False for a dense-only index"
            )
        keyword_kwargs: dict[str, Any] = {
            "top_k": candidate_k,
            "filters": filters,
            "principals": principals,
        }
        if _accepts(index.keyword_search, "source_id"):
            keyword_kwargs["source_id"] = source_id
        keyword_hits = _ranked(
            await index.keyword_search(text, **keyword_kwargs),
            "keyword_rank",
        )
        hits = reciprocal_rank_fusion(dense, keyword_hits)
    if min_score is not None and not hybrid:
        hits = [hit for hit in hits if hit.score >= min_score]
    if max_per_document is not None:
        hits = _limit_per_document(hits, max_per_document)
    if mmr is not None:
        if embedder is None:
            raise ConfigError("mmr requires an embedder")
        if hits:
            vectors = await embedder.embed_batch(
                [Chunk(text=hit.text, source_uri="knowledge:mmr") for hit in hits]
            )
            hits = mmr_select(
                vector,
                hits,
                vectors,
                limit=candidate_k,
                lambda_=mmr.lambda_,
            )
    if rerank is not None:
        hits = await apply_rerank(rerank, text, hits, top_k)
    return hits[:top_k]


def run(
    pipeline: KnowledgePipeline,
    *,
    incremental: bool = False,
    state_path: str | Path | None = None,
    docstore: DocStore | None = None,
    on_error: Literal["fail_fast", "collect"] = "fail_fast",
    on_progress: Callable[[IngestProgress], None] | None = None,
) -> IngestReport:
    """Synchronous convenience wrapper around `KnowledgePipeline.run`."""
    return asyncio.run(
        pipeline.run(
            incremental=incremental,
            state_path=state_path,
            docstore=docstore,
            on_error=on_error,
            on_progress=on_progress,
        )
    )


def _accepts(target: Any, name: str) -> bool:
    """Whether a collaborator's callable accepts a keyword argument."""
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return True
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _signature(value: object | None) -> str | None:
    if value is None:
        return None
    explicit = getattr(value, "signature", None)
    if isinstance(explicit, str):
        return explicit
    attrs = {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
        and isinstance(item, (str, int, float, bool, type(None)))
    }
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    return f"{kind}:{json.dumps(attrs, sort_keys=True)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = [
    "MMR",
    "IngestFailure",
    "IngestProgress",
    "IngestReport",
    "KnowledgePipeline",
    "PipelineResult",
    "query",
    "query_by_vector",
    "run",
]
