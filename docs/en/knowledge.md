# Knowledge base (optional layer)

`reactifact.knowledge` turns heterogeneous documents into a searchable index
without adding a second agent framework. It is **code-first**: a connector is
an object, configuration is ordinary Python, and there is no YAML loader, CLI,
plugin registry or type-string factory.

The layer is intentionally separate from the reactive core. `reactifact` never
imports it; it imports core protocols and reuses the embedder already wired
into `RuntimeResources`:

```text
Connector -> RawDocument -> Document -> Chunk -> EmbeddingProvider -> Index
```

Install-time it is just the core package (`pip install "reactifact[knowledge]"`);
the extra adds no runtime dependencies. The bundled `examples/knowledge` demo
uses this layer for its text corpus.

## Build a pipeline

```python
from reactifact import RuntimeResources
from reactifact.knowledge import (
    KnowledgePipeline,
    ProviderEmbedder,
    SectionsChunker,
    SqliteIndex,
)
from reactifact.providers import embedder_from_env

resources = RuntimeResources(embedder=embedder_from_env())

pipeline = KnowledgePipeline(
    connector,
    chunker=SectionsChunker(max_tokens=400, overlap=60),
    embedder=ProviderEmbedder(resources.embedder),  # core provider
    index=SqliteIndex(".reactifact/knowledge.db"),
)

report = await pipeline.run(
    incremental=True,
    state_path=".reactifact/knowledge/state.json",
)
```

`connector` is any object with a `source_id` and an async `fetch()` returning
`RawDocument`s. Add `list_documents()` and `fetch(uris=...)` to make
incremental runs skip downloading unchanged bodies:

```python
from reactifact.knowledge import DocumentMeta, RawDocument

class DocsConnector:
    source_id = "docs"

    async def list_documents(self) -> list[DocumentMeta]:
        ...

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        ...
```

`RawDocument.from_text(...)` is the common code-first helper. Metadata
(`documentId`, `sourceUrl`, `title`) is merged into every chunk, so citations
survive retrieval.

## Query

```python
hits = await pipeline.query(
    "how do I install it?",
    top_k=5,
    hybrid=True,          # dense + BM25, fused with Reciprocal Rank Fusion
    filters={"space": "ENG"},
    principals=["alice"], # optional ACL check
)
for hit in hits:
    print(hit.source_uri, hit.score, hit.text[:80])
```

`min_score` filters dense cosine results only. Hybrid scores are rank-fusion
scores, not similarities, so a cosine threshold would drop everything — leave
`min_score` unset when `hybrid=True`.

## Rerank

The base hook is domain code: relevance policy is yours. `query` exposes it,
and an optional LLM-backed helper is available below:

```python
from collections.abc import Sequence
from reactifact.knowledge import SearchHit

def prefer_runbooks(
    query: str, hits: Sequence[SearchHit], top_k: int
) -> list[SearchHit]:
    ranked = sorted(
        hits,
        key=lambda hit: hit.metadata.get("kind") != "runbook",
    )
    return [hit.model_copy(update={"score": 1.0 - i / len(ranked)})
            for i, hit in enumerate(ranked[:top_k])]

hits = await pipeline.query(
    "checkout is timing out",
    top_k=5,
    hybrid=True,
    rerank=prefer_runbooks,
)
```

- Called after dense/hybrid retrieval and `min_score`, before the final
  `top_k` cut.
- Receives over-fetched candidates (`top_k * 4`), so it has room to promote or
  filter.
- May be synchronous or asynchronous; return any subset in any order.
- Exceptions propagate. This is application code, and silently falling back to
  the original order would hide relevance bugs. For best-effort retrieval,
  wrap it:

```python
async def safe_rerank(query, hits, top_k):
    try:
        return await my_reranker(query, hits, top_k)
    except Exception:
        logger.warning("domain rerank failed", exc_info=True)
        return list(hits[:top_k])
```

### Ready-made LLM reranker

`llm_reranker` is an opt-in factory, not a separate client: it uses the core
chat `LLMProvider`, which is independent of the embedding provider used to
build the index.

```python
from reactifact.knowledge import llm_reranker

rerank = llm_reranker(
    resources.llm,  # chat provider, not resources.embedder
    instructions="Prefer runbooks; ignore deprecated docs.",
)
hits = await pipeline.query("checkout is timing out", top_k=5, rerank=rerank)
```

A domain prompt can replace both levels:

```python
from reactifact.knowledge import RERANK_OUTPUT_CONTRACT, llm_reranker

def render(query, hits, top_k):
    lines = [f"Query: {query}"]
    for index, hit in enumerate(hits, start=1):
        kind = hit.metadata.get("kind", "doc")
        lines.append(f"[{index}] ({kind}) {hit.text[:800]}")
    return "\n".join(lines)

rerank = llm_reranker(
    resources.llm,
    system_prompt=(
        "You rank NikaRD support excerpts. "
        + RERANK_OUTPUT_CONTRACT
        + " Prefer runbooks over marketing pages; prefer newer docs on ties."
    ),
    user_prompt=render,
)
```

- `system_prompt` replaces the default system prompt — keep
  `RERANK_OUTPUT_CONTRACT` in it.
- `instructions` appends domain rules to the default prompt.
- `user_prompt` replaces the numbered-excerpt renderer and receives full
  `SearchHit` objects, so it can use metadata (`kind`, `documentId`, ...).
- Fails soft: a provider error, invalid JSON or a non-permutation response calls
  `on_error` (or logs a warning) and keeps the original order. The raw
  `rerank=` hook stays fail-loud.
- Hybrid retrieval first: an LLM rerank adds latency, cost and
  non-determinism.

## Indexes

| Index | Persistence | Notes |
| --- | --- | --- |
| `MemoryIndex` | process | tests, small prototypes; supports keyword search |
| `SqliteIndex` | one file | zero infrastructure, brute-force cosine; supports keyword search |
| `QdrantIndex` | remote | raw REST client (no SDK); collection created on demand; hybrid narrows server-side then BM25-ranks locally |

All three implement deterministic point IDs, per-source `reconcile`, metadata
filters and optional ACL visibility. Switching backends does not change the
pipeline.

## Incremental ingestion

`run(incremental=True)` keeps `DocStore` state keyed by **source identity and
a pipeline signature** (extraction, chunking, embedding settings). Changing
chunk size or the embedding model invalidates fingerprints instead of leaving
stale chunks. State is committed only after a successful run.

A connector failure aborts **before** reconciliation, so a transient outage
never makes the corpus look deleted.

## Bridge to the agent runtime

The two integrations are explicit and optional:

```python
from reactifact.knowledge import IndexSource, IngestStep

# retrieval as a regular reactifact Source
source = IndexSource(pipeline, hybrid=True, min_score=0.15)
ctx = Context(resources=RuntimeResources(embedder=..., sources={"kb": source}))

# ingestion as a step (when ingestion belongs to the reactive flow)
step = IngestStep(pipeline)
```

For most services ingestion is a background job, not an agent step: call
`pipeline.run(...)` directly from the scheduler/worker and only expose
`IndexSource` to agents.

## Status page (optional FastAPI router)

Ingestion is a background job, not an agent run, so it does not go into
`RunTrace`. The knowledge layer ships a separate read-only router that reads the
`DocStore` run ledger:

```python
from fastapi import FastAPI
from reactifact.knowledge.web import create_knowledge_router

app = FastAPI()
app.include_router(
    create_knowledge_router(docstore, index=pipeline.index),
    prefix="/knowledge",
)
```

- `GET /knowledge/` — per-source last run, document/chunk counts, errors,
  embed-cache hits, and the recent run history.
- `GET /knowledge/api/knowledge/status` — the same data as JSON.
- `index` is optional; when given, the page also shows the backend type and
  `count()`.

It requires the `web` extra (`pip install "reactifact[web]"`) and is mounted
independently from the traces router. The traces dashboard keeps describing
agent runs; `/knowledge` describes corpus builds.

## What is intentionally absent

- YAML pipelines and a `knowledge` CLI.
- A plugin registry / type strings.
- Answer synthesis, reranking and chat — use `reactifact.quick.rag`,
  `reactifact.providers` and `reactifact.recipes`.
- Evaluation and tracing — use `reactifact.eval` and `reactifact.tracing`.

Keeping those out of `knowledge` is what makes it a capability layer rather
than a second framework inside reactifact.
