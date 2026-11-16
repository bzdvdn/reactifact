"""Build and query a real knowledge base without an external vector service.

Default run is fully offline: SQLite index + the deterministic `HashEmbedder`.
Set an OpenAI-compatible embedding endpoint in the environment and it switches
to `embedder_from_env()`; pass `--qdrant-url` to use Qdrant instead of SQLite.

    python examples/knowledge_base/build.py --docs ./docs/en
    python examples/knowledge_base/build.py --docs ./docs/en --query "sources"
    python examples/knowledge_base/build.py --docs ./docs/en --qdrant-url http://localhost:6333
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from reactifact.knowledge import (
    CachingEmbedder,
    FileEmbeddingCache,
    FilesystemConnector,
    HashEmbedder,
    KnowledgePipeline,
    ProviderEmbedder,
    QdrantIndex,
    SectionsChunker,
    SqliteIndex,
)
from reactifact.knowledge.embed import Embedder
from reactifact.providers import embedder_from_env


def build_embedder(cache_path: Path) -> Embedder:
    provider = embedder_from_env()
    if provider is None:
        return HashEmbedder(dimensions=256)
    return CachingEmbedder(
        ProviderEmbedder(provider),
        cache=FileEmbeddingCache(cache_path),
    )


def build_pipeline(args: argparse.Namespace) -> KnowledgePipeline:
    docs = Path(args.docs)
    index = (
        QdrantIndex(url=args.qdrant_url, collection=args.collection)
        if args.qdrant_url
        else SqliteIndex(args.db)
    )
    return KnowledgePipeline(
        FilesystemConnector(docs, source_id="docs"),
        chunker=SectionsChunker(max_tokens=args.max_tokens, overlap=60),
        embedder=build_embedder(Path(args.cache)),
        index=index,
    )


async def _run(args: argparse.Namespace) -> None:
    pipeline = build_pipeline(args)
    try:
        report = await pipeline.run(
            incremental=True,
            state_path=args.state,
        )
        print(
            f"{pipeline.source_id}: {report.documents_ingested} documents, "
            f"{report.chunks_indexed} chunks indexed"
        )
        print(
            f"  incremental: {report.documents_changed} changed, "
            f"{report.documents_skipped} skipped, "
            f"{report.documents_deleted} deleted"
        )
        if args.query:
            hits = await pipeline.query(args.query, top_k=args.top_k, hybrid=True)
            print(f"\nquery: {args.query}")
            for rank, hit in enumerate(hits, start=1):
                print(f"  [{rank}] {hit.source_uri} (score={hit.score:.4f})")
                print(f"      {hit.text.strip().splitlines()[0][:120]}")
    finally:
        await pipeline.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="docs/en", help="directory of .md files")
    parser.add_argument("--db", default=".reactifact/knowledge.db")
    parser.add_argument("--state", default=".reactifact/knowledge/state.json")
    parser.add_argument("--cache", default=".reactifact/knowledge/embeddings")
    parser.add_argument("--query", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--qdrant-url", default="")
    parser.add_argument("--collection", default="reactifact_docs")
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
