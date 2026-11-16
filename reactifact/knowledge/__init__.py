"""Code-first knowledge ingestion and retrieval for reactifact.

This subpackage is an optional capability layer: it never gets imported by
`reactifact` core. Build a pipeline from explicit collaborators and reuse the
core embedder already wired into `RuntimeResources`::

    from reactifact import RuntimeResources
    from reactifact.providers import embedder_from_env
    from reactifact.knowledge import (
        KnowledgePipeline, ProviderEmbedder, SectionsChunker, SqliteIndex,
    )

    resources = RuntimeResources(embedder=embedder_from_env())
    pipeline = KnowledgePipeline(
        connector,
        chunker=SectionsChunker(max_tokens=400, overlap=60),
        embedder=ProviderEmbedder(resources.embedder),
        index=SqliteIndex("knowledge.db"),
    )
    report = await pipeline.run(incremental=True)

There is no YAML loader, CLI, plugin registry or type-string factory: a
connector is an object, and configuration is ordinary Python.
"""

from __future__ import annotations

from .bridge import (
    IndexSource,
    IngestReportArtifact,
    IngestRequest,
    IngestStep,
)
from .chunk import AutoChunker, Chunker, SectionsChunker, SizeChunker
from .connectors import Connector, FilesystemConnector, ListingConnector
from .docstore import DocEntry, DocStore, RunRecord
from .embed import (
    CachingEmbedder,
    Embedder,
    EmbeddingCache,
    FileEmbeddingCache,
    HashEmbedder,
    InMemoryEmbeddingCache,
    ProviderEmbedder,
    cache_key,
)
from .errors import ConfigError, KnowledgeError, PipelineError, SourceError
from .extract import AutoExtractor, Extractor, HtmlExtractor, MarkdownExtractor
from .indexes import (
    BatchIndexer,
    ChromaIndex,
    Indexer,
    KeywordIndexer,
    MemoryIndex,
    QdrantIndex,
    SqliteIndex,
    reciprocal_rank_fusion,
)
from .models import (
    CCT_SCHEMA_VERSION,
    Chunk,
    Document,
    DocumentMeta,
    RawDocument,
    SearchHit,
    Section,
    content_hash,
)
from .pipeline import IngestReport, KnowledgePipeline, PipelineResult, query, run
from .rerank import (
    DEFAULT_RERANK_SYSTEM,
    RERANK_OUTPUT_CONTRACT,
    RerankFn,
    RerankPrompt,
    RerankResult,
    apply_rerank,
    llm_reranker,
    render_rerank_prompt,
)

__all__ = [
    "AutoChunker",
    "AutoExtractor",
    "BatchIndexer",
    "CCT_SCHEMA_VERSION",
    "CachingEmbedder",
    "ChromaIndex",
    "Chunk",
    "Chunker",
    "ConfigError",
    "Connector",
    "DEFAULT_RERANK_SYSTEM",
    "DocEntry",
    "DocStore",
    "Document",
    "DocumentMeta",
    "Embedder",
    "EmbeddingCache",
    "Extractor",
    "FilesystemConnector",
    "FileEmbeddingCache",
    "HashEmbedder",
    "HtmlExtractor",
    "InMemoryEmbeddingCache",
    "IndexSource",
    "Indexer",
    "IngestReport",
    "IngestReportArtifact",
    "IngestRequest",
    "IngestStep",
    "KeywordIndexer",
    "KnowledgeError",
    "KnowledgePipeline",
    "ListingConnector",
    "MarkdownExtractor",
    "MemoryIndex",
    "PipelineError",
    "PipelineResult",
    "ProviderEmbedder",
    "RERANK_OUTPUT_CONTRACT",
    "RerankFn",
    "RerankPrompt",
    "RerankResult",
    "QdrantIndex",
    "RawDocument",
    "RunRecord",
    "SearchHit",
    "Section",
    "SectionsChunker",
    "SizeChunker",
    "SourceError",
    "SqliteIndex",
    "apply_rerank",
    "cache_key",
    "llm_reranker",
    "content_hash",
    "query",
    "reciprocal_rank_fusion",
    "render_rerank_prompt",
    "run",
]
