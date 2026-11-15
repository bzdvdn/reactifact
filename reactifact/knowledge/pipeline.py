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
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..providers import EmbeddingProvider
from ._closers import aclose_of
from .chunk import AutoChunker, Chunker
from .connectors import Connector, ListingConnector, has_listing
from .docstore import DocEntry, DocStore, RunRecord
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
    ) -> IngestReport:
        """Run the pipeline once.

        With `incremental=True` (or an explicit `docstore`), unchanged
        documents are skipped. A connector failure aborts the run *before*
        reconciliation, so a transient outage never makes the corpus look
        deleted.
        """
        store = docstore
        if store is None and incremental:
            path = Path(state_path) if state_path else DocStore.default_path()
            store = DocStore(path)
        started = time.monotonic()
        signature = self.signature()
        try:
            report = await self._execute(store, signature)
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
        )

    async def aclose(self) -> None:
        """Close owned HTTP clients, if the collaborators expose `aclose`."""
        for collaborator in (self.index, self.embedder):
            closer = aclose_of(collaborator)
            if closer is not None:
                await closer()

    async def _execute(self, docstore: DocStore | None, signature: str) -> IngestReport:
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
        )
        await _fetch(state)
        await _extract(state)
        await _embed(state)
        await _upsert(state)
        await _reconcile(state)
        _commit(state)
        return IngestReport(
            documents_ingested=state.documents,
            chunks_indexed=state.indexed,
            embed_cache_hits=state.embed_cache_hits,
            embed_cache_misses=state.embed_cache_misses,
            documents_changed=state.changed,
            documents_skipped=state.skipped,
            documents_deleted=state.deleted,
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
    for artifact in state.artifacts:
        artifact_hash = content_hash(artifact)
        state.current[artifact.uri] = artifact_hash
        if not state.use_listing:
            state.documents += 1
            stored = (
                state.docstore.entry(state.source_id, state.signature, artifact.uri)
                if state.docstore is not None
                else None
            )
            if stored is not None and stored.content == artifact_hash:
                state.skipped += 1
                state.entries[artifact.uri] = DocEntry(
                    content=artifact_hash, listing=stored.listing
                )
                continue
            state.changed += 1
        listed = state.uri_by_meta.get(artifact.uri)
        state.entries[artifact.uri] = DocEntry(
            content=artifact_hash,
            listing=listed.fingerprint if listed is not None else None,
        )
        try:
            document = await state.extractor.extract(artifact)
            chunks = await state.chunker.chunk(document)
        except (SourceError, ValueError) as exc:
            raise PipelineError(f"failed on artifact {artifact.uri}: {exc}") from exc
        for chunk in chunks:
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
    upsert_batch = getattr(state.index, "upsert_batch", None)
    if callable(upsert_batch):
        batch_index = cast(BatchIndexer, state.index)
        await batch_index.upsert_batch(items, source_id=state.source_id)
        return
    for chunk, vector, artifact_hash in items:
        await state.index.upsert(
            chunk,
            vector,
            source_id=state.source_id,
            artifact_hash=artifact_hash,
        )


async def _reconcile(state: _RunState) -> None:
    if state.index is not None:
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
) -> list[SearchHit]:
    """Retrieve chunks by text, using the pipeline's embedder and index.

    `min_score` filters dense cosine results only. Hybrid scores are
    rank-fusion scores (much smaller, not similarities), so a dense threshold
    applied to them would drop everything; pass it with `hybrid=False`.

    `rerank` runs after retrieval and `min_score`, receives over-fetched
    candidates (``top_k * 4``) and the final `top_k`, and may reorder or filter
    them. It is synchronous or asynchronous. Exceptions propagate: this is
    application code, and silently falling back to the original order would
    make a relevance regression invisible. Callers that need best-effort
    retrieval can wrap their reranker in a `try/except`.
    """
    if isinstance(pipeline, KnowledgePipeline):
        index = pipeline.index
        embedder = pipeline.embedder
    else:
        index = pipeline
        embedder = None
    if index is None:
        raise ConfigError("query requires an index")
    if not text.strip():
        raise ConfigError("query text must not be empty")

    if embedder is None:
        raise ConfigError(
            "query needs a pipeline with an embedder; a bare index cannot "
            "embed the query string"
        )
    vector = await embedder.embed(Chunk(text=text, source_uri="knowledge:query"))
    over_fetch = hybrid or rerank is not None
    candidate_k = top_k * _CANDIDATE_FACTOR if over_fetch else top_k
    dense = await index.search(
        vector,
        top_k=candidate_k,
        filters=filters,
        principals=principals,
    )
    if not hybrid:
        hits = dense
    else:
        keyword_search = getattr(index, "keyword_search", None)
        if callable(keyword_search):
            keyword_index = cast(KeywordIndexer, index)
            keyword_hits = await keyword_index.keyword_search(
                text,
                top_k=candidate_k,
                filters=filters,
                principals=principals,
            )
            hits = reciprocal_rank_fusion(dense, keyword_hits)
        else:
            hits = dense
    if min_score is not None and not hybrid:
        hits = [hit for hit in hits if hit.score >= min_score]
    if rerank is not None:
        hits = await apply_rerank(rerank, text, hits, top_k)
    return hits[:top_k]


def run(
    pipeline: KnowledgePipeline,
    *,
    incremental: bool = False,
    state_path: str | Path | None = None,
    docstore: DocStore | None = None,
) -> IngestReport:
    """Synchronous convenience wrapper around `KnowledgePipeline.run`."""
    return asyncio.run(
        pipeline.run(
            incremental=incremental,
            state_path=state_path,
            docstore=docstore,
        )
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
        if isinstance(item, (str, int, float, bool, type(None)))
    }
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    return f"{kind}:{json.dumps(attrs, sort_keys=True)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = [
    "IngestReport",
    "KnowledgePipeline",
    "PipelineResult",
    "query",
    "run",
]
