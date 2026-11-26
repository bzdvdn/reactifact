"""Optional adapters from the knowledge layer into reactifact primitives.

The knowledge package never imports these into `reactifact` core; the
dependency is one-way (`knowledge -> core`). Use `IndexSource` to expose an
already-built index to RAG recipes/tools, or `IngestStep` to run ingestion from
a reactive pipeline.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel

from .. import Progress, Step
from ..sources import Source, SourceRef
from .chunk import section_text
from .connectors import ListingConnector
from .diversity import MMR
from .errors import ConfigError, KnowledgeError
from .models import Document, RawDocument, SearchHit, Section
from .pipeline import IngestProgress, IngestReport, KnowledgePipeline
from .rerank import RerankFn


class IndexSource(Source):
    """A `reactifact.Source` backed by a knowledge pipeline's index.

    `search` is async (embeddings are async); the synchronous `Source.search`
    raises. Resolving a freshly returned ref uses the hit text cached from the
    search; refs restored from an artifact are resolved by asking the
    connector for their URI.

    `expand` controls what `resolve` returns: the matched chunk (`"chunk"`,
    default), its section (`"section"`) or the whole document (`"document"`).
    Section/document expansion re-fetches the document and runs the pipeline's
    extractor, so no parent text is stored in the index.
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
        max_per_document: int | None = None,
        mmr: MMR | None = None,
        principals: Sequence[str] | Callable[[], Sequence[str]] | None = None,
        require_principals: bool = False,
        expand: Literal["chunk", "section", "document"] = "chunk",
        max_hits: int = 256,
        max_documents: int = 64,
    ) -> None:
        super().__init__(source_id=source_id or f"knowledge:{pipeline.source_id}")
        if expand not in ("chunk", "section", "document"):
            raise ConfigError("expand must be 'chunk', 'section' or 'document'")
        self.pipeline = pipeline
        self.top_k = top_k
        self.hybrid = hybrid
        self.min_score = min_score
        self.rerank = rerank
        self.max_per_document = max_per_document
        self.mmr = mmr
        self.principals = principals
        self.require_principals = require_principals
        self.expand = expand
        self.max_hits = max(1, max_hits)
        self.max_documents = max(1, max_documents)
        self._hits: OrderedDict[str, SearchHit] = OrderedDict()
        self._documents: OrderedDict[str, RawDocument] = OrderedDict()
        self.preferred = True

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Synchronous search is unsupported; use `asearch`."""
        raise NotImplementedError(
            "IndexSource requires async asearch (embedding is async)"
        )

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        principals = self.principals() if callable(self.principals) else self.principals
        if principals is None and self.require_principals:
            raise ConfigError(
                "IndexSource.require_principals is set, but no principals "
                "were resolved for this search"
            )
        hits = await self.pipeline.query(
            query,
            top_k=limit or self.top_k,
            hybrid=self.hybrid,
            min_score=self.min_score,
            rerank=self.rerank,
            max_per_document=self.max_per_document,
            mmr=self.mmr,
            principals=principals,
        )
        refs: list[SourceRef] = []
        for hit in hits:
            self._remember(hit)
            refs.append(
                SourceRef(
                    source_id=self.source_id,
                    locator=hit.source_uri,
                    fragment=hit.chunk_id,
                    score=hit.score,
                    title=str(hit.metadata.get("title") or hit.source_uri),
                    excerpt=hit.text[:200],
                    metadata={
                        **hit.metadata,
                        "retrieval": {
                            "score_kind": hit.score_kind,
                            "dense_rank": hit.dense_rank,
                            "keyword_rank": hit.keyword_rank,
                            "rerank_rank": hit.rerank_rank,
                        },
                    },
                )
            )
        return refs

    def _remember(self, hit: SearchHit) -> None:
        key = hit.chunk_id
        self._hits[key] = hit
        self._hits.move_to_end(key)
        while len(self._hits) > self.max_hits:
            self._hits.popitem(last=False)

    async def resolve(self, ref: SourceRef) -> str:
        """Resolve a ref to chunk, section or document text (see `expand`)."""
        if self.expand == "chunk":
            cached = self._hits.get(ref.fragment or ref.locator)
            if cached is not None:
                return cached.text
            raw = await self._fetch_raw(ref.locator)
            if raw is None:
                raise KnowledgeError(
                    f"cannot resolve {ref.locator!r}: not in the last search "
                    "and the connector did not return it"
                )
            return raw.data.decode("utf-8", errors="replace")

        document = await self._extract(ref.locator)
        if self.expand == "document":
            return "\n\n".join(
                text
                for section in document.sections
                if (text := section_text(document, section))
            )
        return section_text(document, self._section_for(ref, document))

    def _section_for(self, ref: SourceRef, document: Document) -> Section:
        index = ref.metadata.get("section_index")
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        if isinstance(index, int) and 0 <= index < len(document.sections):
            return document.sections[index]
        heading = ref.metadata.get("heading")
        if heading:
            for section in document.sections:
                if section.heading == heading:
                    return section
        raise KnowledgeError(
            f"cannot expand {ref.locator!r}: section {index!r} not found"
        )

    async def _extract(self, uri: str) -> Document:
        raw = await self._fetch_raw(uri)
        if raw is None:
            raise KnowledgeError(
                f"cannot expand {uri!r}: the connector did not return it"
            )
        return await self.pipeline.extractor.extract(raw)

    async def _fetch_raw(self, uri: str) -> RawDocument | None:
        cached = self._documents.get(uri)
        if cached is not None:
            self._documents.move_to_end(uri)
            return cached
        connector = self.pipeline.connector
        try:
            if isinstance(connector, ListingConnector):
                documents = await connector.fetch(uris={uri})
            else:
                documents = await connector.fetch()
        except Exception as exc:  # noqa: BLE001 — connector failure is domain
            raise KnowledgeError(f"cannot fetch {uri!r} for expansion: {exc}") from exc
        for document in documents:
            if document.uri == uri or document.step_id == uri:
                self._documents[uri] = document
                self._documents.move_to_end(uri)
                while len(self._documents) > self.max_documents:
                    self._documents.popitem(last=False)
                return document
        return None


class IngestRequest(BaseModel):
    """Input artifact for `IngestStep`."""

    incremental: bool = True
    state_path: str | None = None
    on_error: Literal["fail_fast", "collect"] = "fail_fast"
    batch_size: int | None = None


class IngestReportArtifact(BaseModel):
    """Output artifact written by `IngestStep`."""

    source_id: str
    documents_ingested: int
    chunks_indexed: int
    documents_changed: int = 0
    documents_skipped: int = 0
    documents_deleted: int = 0
    documents_failed: int = 0
    failures: list[dict[str, str]] = []
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0


class IngestStep(Step[IngestReportArtifact]):
    """Runs a `KnowledgePipeline` as a reactifact step."""

    consumes = (IngestRequest,)

    def __init__(self, pipeline: KnowledgePipeline) -> None:
        super().__init__(produces=IngestReportArtifact, consumes=(IngestRequest,))
        self.pipeline = pipeline

    async def run(
        self, request: IngestRequest, progress: Progress
    ) -> IngestReportArtifact:
        """Run ingestion and return the report artifact."""

        def report_progress(event: IngestProgress) -> None:
            if event.stage == "document":
                return
            progress(
                f"knowledge {event.source_id}: {event.stage} "
                f"({event.documents} docs, {event.chunks} chunks, "
                f"{event.failed} failed)"
            )

        report = await self.pipeline.run(
            incremental=request.incremental,
            state_path=request.state_path,
            on_error=request.on_error,
            on_progress=report_progress,
            batch_size=request.batch_size,
        )
        return _artifact(self.pipeline, report)


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
        documents_failed=report.documents_failed,
        failures=[
            {"uri": failure.uri, "stage": failure.stage, "error": failure.error}
            for failure in report.failures
        ],
        embed_cache_hits=report.embed_cache_hits,
        embed_cache_misses=report.embed_cache_misses,
    )


__all__ = [
    "IndexSource",
    "IngestReportArtifact",
    "IngestRequest",
    "IngestStep",
]
