"""Optional adapters from the knowledge layer into reactifact primitives.

The knowledge package never imports these into `reactifact` core; the
dependency is one-way (`knowledge -> core`). Use `IndexSource` to expose an
already-built index to RAG recipes/tools, or `IngestStep` to run ingestion from
a reactive pipeline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from pydantic import BaseModel

from .. import Effects, Step
from ..sources import Source, SourceRef
from .errors import KnowledgeError
from .models import RawDocument, SearchHit
from .pipeline import IngestReport, KnowledgePipeline
from .rerank import RerankFn


class IndexSource(Source):
    """A `reactifact.Source` backed by a knowledge pipeline's index.

    `search` is async (embeddings are async); the synchronous `Source.search`
    raises. Resolving a freshly returned ref uses the hit text cached from the
    search; refs restored from an artifact are resolved by asking the
    connector for their URI.
    """

    def __init__(
        self,
        pipeline: KnowledgePipeline,
        *,
        source_id: str | None = None,
        top_k: int = 10,
        hybrid: bool = False,
        min_score: float | None = None,
        rerank: RerankFn | None = None,
    ) -> None:
        super().__init__(source_id=source_id or f"knowledge:{pipeline.source_id}")
        self.pipeline = pipeline
        self.top_k = top_k
        self.hybrid = hybrid
        self.min_score = min_score
        self.rerank = rerank
        self._hits: dict[str, SearchHit] = {}
        self.preferred = True

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        raise NotImplementedError(
            "IndexSource requires async asearch (embedding is async)"
        )

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        hits = await self.pipeline.query(
            query,
            top_k=limit or self.top_k,
            hybrid=self.hybrid,
            min_score=self.min_score,
            rerank=self.rerank,
        )
        refs: list[SourceRef] = []
        for hit in hits:
            self._hits[hit.source_uri] = hit
            refs.append(
                SourceRef(
                    source_id=self.source_id,
                    locator=hit.source_uri,
                    score=hit.score,
                    title=str(hit.metadata.get("title") or hit.source_uri),
                    excerpt=hit.text[:200],
                    metadata=dict(hit.metadata),
                )
            )
        return refs

    async def resolve(self, ref: SourceRef) -> str:
        cached = self._hits.get(ref.locator)
        if cached is not None:
            return cached.text
        text = await self._fetch_text(ref.locator)
        if text is None:
            raise KnowledgeError(
                f"cannot resolve {ref.locator!r}: not in the last search and "
                "the connector cannot fetch it"
            )
        return text

    async def _fetch_text(self, uri: str) -> str | None:
        connector = self.pipeline.connector
        fetch = getattr(connector, "fetch", None)
        if not callable(fetch):
            return None
        fetch_fn = cast(Callable[..., Awaitable[list[RawDocument]]], fetch)
        try:
            documents = await fetch_fn(uris={uri})
        except TypeError:
            documents = await fetch_fn()
        for document in documents:
            if document.uri == uri or document.step_id == uri:
                return document.data.decode("utf-8", errors="replace")
        return None


class IngestRequest(BaseModel):
    """Input artifact for `IngestStep`."""

    incremental: bool = True
    state_path: str | None = None


class IngestReportArtifact(BaseModel):
    """Output artifact written by `IngestStep`."""

    source_id: str
    documents_ingested: int
    chunks_indexed: int
    documents_changed: int = 0
    documents_skipped: int = 0
    documents_deleted: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0


class IngestStep(Step[IngestReportArtifact]):
    """Runs a `KnowledgePipeline` as a reactifact step."""

    consumes = (IngestRequest,)

    def __init__(self, pipeline: KnowledgePipeline) -> None:
        super().__init__(produces=IngestReportArtifact, consumes=(IngestRequest,))
        self.pipeline = pipeline

    async def run(
        self,
        request: IngestRequest,
        effects: Effects,
    ) -> None:
        report = await self.pipeline.run(
            incremental=request.incremental,
            state_path=request.state_path,
        )
        effects.create(_artifact(self.pipeline, report))


def _artifact(
    pipeline: KnowledgePipeline, report: IngestReport
) -> IngestReportArtifact:
    return IngestReportArtifact(
        source_id=pipeline.source_id,
        documents_ingested=report.documents_ingested,
        chunks_indexed=report.chunks_indexed,
        documents_changed=report.documents_changed,
        documents_skipped=report.documents_skipped,
        documents_deleted=report.documents_deleted,
        embed_cache_hits=report.embed_cache_hits,
        embed_cache_misses=report.embed_cache_misses,
    )


__all__ = [
    "IndexSource",
    "IngestReportArtifact",
    "IngestRequest",
    "IngestStep",
]
