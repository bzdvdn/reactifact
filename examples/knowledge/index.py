"""Knowledge-module backend for the `knowledge` demo.

The demo's text corpus (``docs/guide`` + ``docs/pricing``) is built and queried
through `reactifact.knowledge`; the CSV cost table stays a structured
`CSVSource`, because a spreadsheet must remain structured (§29, §67).

The index is created lazily on the first search, so `build_resources()` stays
synchronous and the CLI, web app and scenario lab share one code path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from reactifact.knowledge import (
    DocStore,
    Embedder,
    FilesystemConnector,
    HashEmbedder,
    IndexSource,
    KnowledgePipeline,
    SectionsChunker,
    SqliteIndex,
)
from reactifact.sources import SourceRef

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
KNOWLEDGE_DOCS = ROOT / "docs"
KNOWLEDGE_DATA = ROOT / ".knowledge"

CHUNK_MAX_TOKENS = 400
CHUNK_OVERLAP = 60


class LazyKnowledgeSource(IndexSource):
    """An `IndexSource` that builds/refreshes the index before its first search."""

    def __init__(
        self,
        pipeline: KnowledgePipeline,
        docstore: DocStore,
        *,
        source_id: str = "knowledge",
    ) -> None:
        super().__init__(pipeline, source_id=source_id, hybrid=True)
        self.docstore = docstore
        self._ready = False
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        """Run incremental ingestion once; safe under concurrent searches."""
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            await self.pipeline.run(incremental=True, docstore=self.docstore)
            self._ready = True
            logger.info(
                "knowledge demo: indexed %s",
                self.pipeline.source_id,
            )

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        await self.ensure_ready()
        return await super().asearch(query, limit)

    async def resolve(self, ref: SourceRef) -> str:
        await self.ensure_ready()
        return await super().resolve(ref)


@dataclass
class KnowledgeBackend:
    """The pipeline, ledger and source the demo shares across transports."""

    pipeline: KnowledgePipeline
    docstore: DocStore
    source: LazyKnowledgeSource

    async def ensure_ready(self) -> None:
        await self.source.ensure_ready()

    async def aclose(self) -> None:
        await self.pipeline.aclose()


def build_backend(
    *,
    docs: Path | None = None,
    data_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> KnowledgeBackend:
    """Build the text index backend. Defaults are offline and deterministic."""
    data = Path(data_dir) if data_dir else KNOWLEDGE_DATA
    data.mkdir(parents=True, exist_ok=True)
    pipeline = KnowledgePipeline(
        FilesystemConnector(
            Path(docs) if docs else KNOWLEDGE_DOCS,
            source_id="knowledge",
        ),
        chunker=SectionsChunker(
            max_tokens=CHUNK_MAX_TOKENS,
            overlap=CHUNK_OVERLAP,
        ),
        embedder=embedder or HashEmbedder(dimensions=256),
        index=SqliteIndex(str(data / "knowledge.db")),
    )
    docstore = DocStore(data / "state.json")
    source = LazyKnowledgeSource(pipeline, docstore)
    return KnowledgeBackend(pipeline=pipeline, docstore=docstore, source=source)


__all__ = [
    "KNOWLEDGE_DATA",
    "KNOWLEDGE_DOCS",
    "KnowledgeBackend",
    "LazyKnowledgeSource",
    "build_backend",
]
