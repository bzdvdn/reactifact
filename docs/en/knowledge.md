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

## Source vs Connector

Two capabilities, two lifecycles:

| | `Source` (`reactifact.sources`) | `Connector` (`reactifact.knowledge`) |
| --- | --- | --- |
| When | query time, inside an agent | ingestion time, before queries |
| Does | `search(query)` + `resolve(ref)` | `fetch()` / `list_documents()` |
| Returns | `SourceRef` | `RawDocument` |
| Example | `CSVSource`, `WebSource` | `FilesystemConnector`, your API connector |

`IndexSource` is the bridge: it turns an already-built knowledge index into a
regular `Source` for `RuntimeResources.sources`.
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
survive retrieval. Built-in chunkers also add `section_index`, `chunk_index`
and `heading` coordinates, which is enough to render and highlight a citation.

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

Retrieval is scoped to the pipeline's `source_id`; in a shared index, another
source's chunks never leak into the result. Pass `source_id=` to query a
different source explicitly, or pass a bare index plus `embedder=` to
`reactifact.knowledge.query(...)`.

`min_score` filters dense cosine results only. Hybrid scores are rank-fusion
scores, not similarities, so `min_score` with `hybrid=True` raises `ConfigError`
instead of silently dropping results. `hybrid=True` also requires an index with
`keyword_search`; dense-only indexes must use `hybrid=False`.

`max_per_document=N` keeps at most `N` chunks of the same document in the
result, so one long document cannot crowd out everything else. Documents are
identified by `document_id`/`documentId` metadata, falling back to
`source_uri`. The cap is applied before `rerank`, so the reranker sees a
diverse candidate set.

`mmr=MMR(lambda_=0.3)` re-ranks over-fetched candidates by relevance **and**
novelty (Maximal Marginal Relevance). `lambda_=1` is pure relevance, `0` is
pure novelty; the pipeline embeds candidate texts with its own embedder, after
`min_score`/`max_per_document` and before `rerank`. `query_by_vector` requires
`embedder=` when MMR is used.

Every hit carries retrieval provenance: `dense_rank`, `keyword_rank`,
`rerank_rank`, `score_kind` (`"dense"` or `"rrf"`) and a stable `chunk_id`.
`IndexSource` puts them under `SourceRef.metadata["retrieval"]` and uses the
chunk id as the ref `fragment`, so two chunks of one document get distinct
artifact ids.

Already have a vector? `query_by_vector(...)` runs the same retrieval without
embedding the text. Pass `query_text=` when using `hybrid=True` (BM25 needs
the words) or `rerank=` (the reranker needs the query).

## Retrieval metrics

`reactifact.eval.retrieval` scores ranking quality without an answer:
recall@k, precision@k, MRR, nDCG@k and hit rate. `reactifact.knowledge.eval`
wires a pipeline to those metrics:

```python
from reactifact.eval import RetrievalCase
from reactifact.knowledge import evaluate_pipeline

report = await evaluate_pipeline(
    pipeline,
    [
        RetrievalCase("how do I install?", relevant=["install.md"]),
        RetrievalCase("refund policy", relevant={"refund.md": 2.0}),
    ],
    k=5,
    hybrid=True,
)
print(report.render())
report.assert_passed({"recall": 0.9})   # CI gate
```

`relevant` is a list (binary) or a mapping (graded gains). The default key
compares documents (`documentId`, falling back to `source_uri`); pass
`key=chunk_key` for chunk-level metrics. `cases_from_dataset(dataset)` builds
cases from `inputs["query"]` and `reference_outputs["relevant"]`. A case
without relevant ids is skipped, never scored as zero.

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
| `PgVectorIndex` | Postgres | `pg` extra; requires `CREATE EXTENSION vector`; cosine; server-side metadata/ACL filters and `tsvector` keyword search |
| `ChromaIndex` | local dir / remote client | optional `chromadb` extra; cosine distance; dense only (`hybrid=True` raises `ConfigError`); pass `client=` to reuse an existing Chroma client |

All backends implement deterministic point IDs, per-source `reconcile`,
metadata filters and optional ACL visibility. Point IDs include the chunker's
`chunk_index`, so identical bodies in two sections stay distinct. Switching backends does not change
the pipeline.

Built-in indexes validate vector dimensionality: the first upsert pins the
size, and a later vector of a different size raises `ConfigError` before
anything is written. A changed embedding model therefore fails at the boundary
instead of deep inside a database client. `ChromaIndex` additionally relies on
Chroma's own collection check for existing data.

Optional capabilities are runtime-checkable protocols, not `getattr` probes:
`KeywordIndexer` (`keyword_search`), `BatchIndexer` (`upsert_batch`) and
`CountableIndex` (`count`). An adapter implements only what it supports; the
pipeline checks the protocol and uses the capability or raises a clear
`ConfigError`.

`PgVectorIndex` requires `pip install "reactifact[pg]"` and a database with the
pgvector extension. Pass `dsn=` or `dsn_env=`; `text_search_config` defaults to
`simple` (language-neutral) and can be `english` for stemming. `ChromaIndex` is
optional: `pip install "reactifact[knowledge-chroma]"`. Metadata values are
stringified for Chroma's scalar-only metadata; the full original metadata is
preserved under `__metadata` and returned in `SearchHit.metadata`.

## Incremental ingestion

`run(incremental=True)` keeps `DocStore` state keyed by **source identity and
a pipeline signature** (extraction, chunking, embedding settings). Changing
chunk size or the embedding model invalidates fingerprints instead of leaving
stale chunks. The document hash covers content type and metadata as well as
bytes, so an ACL/title/tag change re-indexes even when the text is unchanged.
State is committed only after a successful run. Runs are serialized per
`(state_path, source_id)` within the process; across processes the contract is
one ingestion writer per source (use an external lock if you fan out).

A connector failure aborts **before** reconciliation, so a transient outage
never makes the corpus look deleted.

### DocStore backends

`DocStore` is a protocol. `JsonDocStore` (the default behind `state_path=`) is
one JSON file rewritten on save — fine for small corpora. `SqliteDocStore`
keeps the same fingerprints and run ledger in SQLite with transactional
per-row writes:

```python
from reactifact.knowledge import SqliteDocStore

docstore = SqliteDocStore(".reactifact/knowledge/state.sqlite3")
await pipeline.run(docstore=docstore)
```

Both expose `entry`/`commit`/`save`/`record_run`/`runs`/`snapshot`, so the
pipeline and the status router do not care which backend is used. Pass
`docstore=` explicitly to choose SQLite; `state_path=` keeps using JSON.

### Failure isolation, progress and concurrency

By default a bad document aborts the run (`on_error="fail_fast"`). For large
corpora, collect failures and keep going:

```python
from reactifact.knowledge import IngestProgress

events: list[IngestProgress] = []
report = await pipeline.run(
    incremental=True,
    on_error="collect",
    on_progress=events.append,
)
report.documents_failed       # count
report.failures               # [IngestFailure(uri, stage, error), ...]
```

A failed document is **not committed** to `DocStore`; its previous index points
are kept and the next incremental run retries it. `on_progress` receives stage
events (`start`, `fetch`, `extract`, `embed`, `index`, `reconcile`, `done`) plus
per-document/failed events with counts.

`document_concurrency` (default `1`) parallelizes extract/chunk across
documents; progress events arrive as each document finishes. Connector fetching
remains owned by the connector; `ProviderEmbedder` already batches/concurrently
embeds.

`run(..., batch_size=N)` bounds ingestion memory: with a `ListingConnector`
only one batch of bodies is fetched, extracted, embedded and upserted at a
time, and per-batch pending chunks/vectors are released. State is still
committed once at the end; a fail-fast error leaves earlier batches indexed but
unreconciled, so the next run repairs them.

Without a `DocStore` (non-incremental runs) a failed document has no previous
hash to restore, so `on_error="collect"` skips deletion reconciliation for that
run: successful documents are still upserted, but nothing is deleted. Use
`incremental=True` for the full keep-and-retry behavior.

## Bridge to the agent runtime

The two integrations are explicit and optional:

```python
from reactifact.knowledge import IndexSource, IngestStep

# retrieval as a regular reactifact Source
source = IndexSource(pipeline, hybrid=True)
ctx = Context(resources=RuntimeResources(embedder=..., sources={"kb": source}))

# ingestion as a step (when ingestion belongs to the reactive flow)
step = IngestStep(pipeline)  # returns an IngestReportArtifact
```

For most services ingestion is a background job, not an agent step: call
`pipeline.run(...)` directly from the scheduler/worker and only expose
`IndexSource` to agents.

`IndexSource(expand="section")` (or `"document"`) makes `resolve` return the
parent section/document instead of the matched chunk: it re-fetches the source
document and runs the same extractor, so parent text is never stored in the
index. Expansion uses per-URI `fetch(uris=...)` for `ListingConnector`s and a
full fetch for plain connectors; a missing document or section raises
`KnowledgeError`, which `materialize_doc` reports through `on_error`.

`IndexSource(principals=["alice"])` (or a callable returning principals)
applies ACL filtering to every search, so RAG cannot silently query without an
identity. `require_principals=True` turns a missing identity into a
`ConfigError` instead of an unfiltered search.

## Custom connectors and indexes

The built-in `FilesystemConnector` covers the common local-docs case:

```python
from reactifact.knowledge import FilesystemConnector, KnowledgePipeline

pipeline = KnowledgePipeline(
    FilesystemConnector("docs", source_id="docs"),
    embedder=embedder,
    index=index,
)
```

It provides content fingerprints and `fetch(uris=...)`, so incremental runs
skip unchanged files. A missing root raises `SourceError` instead of treating
the corpus as empty and reconciling it away.

### Built-in connectors and extractors

| Connector | Notes |
| --- | --- |
| `FilesystemConnector` | text files under a directory; content hashes; per-URI fetch |
| `HttpConnector` | explicit URLs or a sitemap; `ETag`/`Last-Modified` fingerprints; `httpx` is a core dependency |
| `S3Connector` | one bucket with an optional prefix; `ETag` listing; `knowledge-s3` (`boto3`) |

| Extractor | Input | Extra |
| --- | --- | --- |
| `MarkdownExtractor` | Markdown / plain text | — |
| `HtmlExtractor` | static HTML | — |
| `CsvExtractor` | CSV → Markdown table sections | — |
| `JsonExtractor` | JSON objects/arrays → sections | — |
| `PdfExtractor` | one section per PDF page | `knowledge-pdf` (`pypdf`) |
| `DocxExtractor` | DOCX headings, paragraphs, tables | `knowledge-office` (`python-docx`) |

`AutoExtractor` dispatches by content type, then by URI suffix. Optional
extractors are imported lazily and the error names the extra to install.

```python
from reactifact.knowledge import HttpConnector

pipeline = KnowledgePipeline(
    HttpConnector(sitemap="https://docs.example/sitemap.xml"),
    embedder=embedder,
    index=index,
)
```

There is no registry to extend — implement the protocol and pass an instance:

- **Source connectors** satisfy `Connector` (`source_id` + `fetch()`), or
  `ListingConnector` (adds `list_documents()` and `fetch(uris=...)` for cheap
  incremental skips).
- **Vector stores** satisfy `Indexer` (`upsert`, `reconcile`, `search`); add
  `KeywordIndexer.keyword_search` for hybrid retrieval,
  `BatchIndexer.upsert_batch` for bulk writes and `CountableIndex.count` for
  the status page. These are runtime-checkable protocols: implementing the
  method is the whole registration step.

`MemoryIndex`, `SqliteIndex`, `QdrantIndex`, `PgVectorIndex` and `ChromaIndex`
are reference implementations of the same contract; a Milvus adapter is just
another class passed as `index=`.

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
