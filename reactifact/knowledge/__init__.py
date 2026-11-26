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
from .connectors import (
    Connector,
    FilesystemConnector,
    HttpConnector,
    ListingConnector,
    S3Connector,
)
from .diversity import MMR, mmr_select
from .docstore import (
    DocEntry,
    DocStore,
    JsonDocStore,
    RunRecord,
    SqliteDocStore,
)
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
from .eval import (
    cases_from_dataset,
    chunk_key,
    document_key,
    evaluate_pipeline,
)
from .extract import (
    AutoExtractor,
    CsvExtractor,
    DocxExtractor,
    Extractor,
    HtmlExtractor,
    JsonExtractor,
    MarkdownExtractor,
    PdfExtractor,
)
from .indexes import (
    BatchIndexer,
    ChromaIndex,
    CountableIndex,
    Indexer,
    KeywordIndexer,
    MemoryIndex,
    PgVectorIndex,
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
from .pipeline import (
    IngestFailure,
    IngestProgress,
    IngestReport,
    KnowledgePipeline,
    PipelineResult,
    query,
    query_by_vector,
    run,
)
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
    "CountableIndex",
    "CCT_SCHEMA_VERSION",
    "CachingEmbedder",
    "CsvExtractor",
    "ChromaIndex",
    "Chunk",
    "Chunker",
    "ConfigError",
    "Connector",
    "DEFAULT_RERANK_SYSTEM",
    "DocEntry",
    "DocStore",
    "JsonDocStore",
    "Document",
    "DocumentMeta",
    "DocxExtractor",
    "Embedder",
    "EmbeddingCache",
    "Extractor",
    "FilesystemConnector",
    "FileEmbeddingCache",
    "HashEmbedder",
    "HtmlExtractor",
    "HttpConnector",
    "InMemoryEmbeddingCache",
    "IndexSource",
    "Indexer",
    "IngestFailure",
    "IngestProgress",
    "IngestReport",
    "IngestReportArtifact",
    "IngestRequest",
    "IngestStep",
    "JsonExtractor",
    "KeywordIndexer",
    "KnowledgeError",
    "KnowledgePipeline",
    "ListingConnector",
    "MarkdownExtractor",
    "MMR",
    "mmr_select",
    "MemoryIndex",
    "PipelineError",
    "PgVectorIndex",
    "PdfExtractor",
    "PipelineResult",
    "ProviderEmbedder",
    "RERANK_OUTPUT_CONTRACT",
    "RerankFn",
    "RerankPrompt",
    "RerankResult",
    "QdrantIndex",
    "RawDocument",
    "RunRecord",
    "SqliteDocStore",
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
    "cases_from_dataset",
    "chunk_key",
    "document_key",
    "evaluate_pipeline",
    "query",
    "query_by_vector",
    "reciprocal_rank_fusion",
    "render_rerank_prompt",
    "run",
]
