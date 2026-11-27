---
name: reactifact-rag
description: Retrieve external content in reactifact — configure Sources (filesystem, CSV, web), use the reactifact.knowledge ingestion/retrieval layer (connectors, extractors, hybrid search, MMR, metrics), fan out and rank with fan_out_sources, materialize documents with provenance, and compose RAG, routing, plan-execute and reflection recipes. Use whenever a reactifact pipeline must search files/documents/the web, do retrieval-augmented generation, resolve citations, or when the user mentions sources, retrieval, RAG, chunking, embeddings, knowledge ingestion or a scout/resolver step. Not for prompt or tool-loop design (`reactifact-llm`).
---

# Retrieval and recipes in reactifact

Retrieval is a **source capability**, not agent logic. A `Source` knows how to
`search(query)` and `resolve(ref)`; an agent just consumes a question and asks
its sources. This keeps the search mechanics (keyword, vector, SQL, HTTP) out of
the pipeline and swappable per environment.

## Sources

Register sources on `RuntimeResources(sources={...})`; an agent retrieves via
`fan_out_sources` and materializes via `materialize_doc`.

| Source | Search |
| --- | --- |
| `FileSystemSource(root, source_id=..., scorer=...)` | lexical over `.md`/`.txt` |
| `CSVSource(root, source_id=..., scorer=...)` | lexical over rows; `resolve` returns a row dict |
| `WebSource(urls=[...])` | fetch + keyword |

Vector retrieval is the optional knowledge layer (`KnowledgePipeline` +
`IndexSource`), not a built-in `Source`; a custom `Source` may still override
`asearch` for embeddings.

`SourceRef` (an artifact you can save and link) carries `source_id`, `locator`,
`fragment`, `score`, `title`, `excerpt`, `query_id`, `metadata`; `fragment`
distinguishes chunks of one document and participates in `stable_id()`.

## Fan out → materialize (the RAG shape)

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Context, Effects, Runtime, RuntimeResources, Step, create_step_group,
)
from reactifact.recipes import fan_out_sources
from reactifact.sources import Source, SourceRef


class Question(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class MemorySource(Source):
    """A tiny source: search over a list of docs, resolve by index."""

    def __init__(self, docs, source_id="memory"):
        super().__init__(source_id=source_id)
        self._docs = docs

    def search(self, query, limit=10):
        terms = query.lower().split()
        scored = [
            (i, doc, sum(term in doc.lower() for term in terms))
            for i, doc in enumerate(self._docs)
        ]
        hits = [(i, doc, score) for i, doc, score in scored if score]
        return [
            SourceRef(
                source_id=self.source_id,
                locator=str(i),
                title=doc,
                score=float(score),
            )
            for i, doc, score in hits[:limit]
        ]

    async def resolve(self, ref):
        return self._docs[int(ref.locator)]


class Scout(Step):
    consumes = (Question,)
    produces = (Note, SourceRef)  # fan_out_sources also writes SourceRef artifacts

    async def run(
        self, context: Context, question: Question, effects: Effects
    ) -> None:
        refs = await fan_out_sources(context, question.text, owner_id="q1")
        for ref in refs:
            effects.create(Note(text=f"{ref.title} (score={ref.score})"))


ctx = Context(
    resources=RuntimeResources(
        sources={"memory": MemorySource(["gpu costs guide", "cpu notes"])}
    )
)
ctx.create(Question(text="gpu costs"))
agent = create_step_group("scout", steps=[Scout()])
asyncio.run(Runtime(ctx, groups=[agent]).arun())

assert ctx.list_artifacts(Note)              # ranked hits became artifacts
assert ctx.list_artifacts(SourceRef)         # the refs were created too
```

`fan_out_sources` polls every configured source, ranks by score, and writes
**idempotent** `SourceRef` artifacts (`id=f"ref:{stable_id}:{owner}"`) into the
current effects — a repeated fan-out for the same owner is a create-or-refresh,
not a duplicate. `on_start`/`on_count` hooks let you announce progress.

To turn a ref into a real document with provenance, call `materialize_doc`:

```python
# not-run: illustrative
from reactifact import Artifact, Context, Emit
from reactifact.recipes import materialize_doc


async def run(
    self,
    context: Context,
    ref: Artifact[SourceRef],
) -> Emit | None:
    doc = await materialize_doc(
        context, ref, lambda ctx, source, content: Document(text=content)
    )
    if doc is None:
        return None
    return Emit(
        Answer(text=summarize(doc.text)),
        links=(("supported_by", ref),),
    )
```

It creates the document with a stable id and links it `materialized_from →
SourceRef` (configurable relation). A missing source or resolve failure is a
`None`, not a crash.

## Knowledge layer (`reactifact.knowledge`)

Use `reactifact.knowledge` when retrieval is over a maintained document corpus
rather than a handful of rows: it owns ingestion (`Connector -> RawDocument ->
Document -> Chunk -> Embedder -> Index`), incremental fingerprints, hybrid
search, ACLs and retrieval metrics. It is imported lazily and adds no runtime
dependencies.

```python
import asyncio
from reactifact.eval import RetrievalCase
from reactifact.knowledge import (
    HashEmbedder, KnowledgePipeline, MemoryIndex, RawDocument,
    evaluate_pipeline,
)

class DictConnector:
    source_id = "docs"
    def __init__(self, docs):
        self.docs = docs
    async def fetch(self):
        return [
            RawDocument.from_text(step_id=uri, uri=uri, text=text, title=uri)
            for uri, text in self.docs.items()
        ]

pipeline = KnowledgePipeline(
    DictConnector({
        "guide.md": "# Install\n\nRun pip install reactifact.\n\n"
                    "# Refunds\n\nRefunds within 30 days.",
    }),
    embedder=HashEmbedder(dimensions=64),
    index=MemoryIndex(),
)
report = asyncio.run(pipeline.run())
hits = asyncio.run(pipeline.query("refund", top_k=2, hybrid=True))
assert report.chunks_indexed > 0
assert hits and hits[0].chunk_id
assert all(hit.score_kind == "rrf" for hit in hits)

metrics = asyncio.run(
    evaluate_pipeline(
        pipeline,
        [RetrievalCase("refund", ["guide.md"])],
        k=1,
    )
)
assert metrics.aggregate()["mrr"] == 1.0
```

### Ingestion

| Built-in | Notes |
| --- | --- |
| `FilesystemConnector` | text files under a directory; content hashes |
| `HttpConnector` | explicit URLs or a sitemap; `ETag`/`Last-Modified` |
| `S3Connector` | one bucket; `ETag`; optional `knowledge-s3` |

`AutoExtractor` handles Markdown/HTML/CSV/JSON out of the box; PDF and DOCX
need `knowledge-pdf` (`pypdf`) and `knowledge-office` (`python-docx`).

`run(incremental=True, state_path=...)` keeps fingerprints in `DocStore`
(`JsonDocStore` by default, `SqliteDocStore` for scale). For large corpora use
`run(batch_size=N)` to bound memory and `on_error="collect"` to isolate bad
documents; `on_progress` receives `IngestProgress` events.

### Query

`query(text, ...)` supports `hybrid=True`, `filters`, `principals` (ACL),
`max_per_document`, `mmr=MMR(lambda_=0.3)` and `rerank`. Hits carry retrieval
provenance: `dense_rank`, `keyword_rank`, `rerank_rank`, `score_kind` and a
stable `chunk_id`. `query_by_vector` reuses a precomputed query vector; MMR
then needs `embedder=`.

### Bridging into agents

`IndexSource(pipeline, expand="section", principals=["alice"],
require_principals=True)` exposes the index as a regular `Source` for
`fan_out_sources` / `materialize_doc`. `expand` returns the parent
section/document on `resolve`; `principals` applies ACL filtering to every
search, and `require_principals` turns a missing identity into an error.

`reactifact.eval` ships retrieval metrics (recall, precision, MRR, nDCG, hit
rate); `reactifact.knowledge.eval` adds `cases_from_dataset` for dataset-driven
retrieval evals.

## Recipes (ready-made patterns)

`reactifact.recipes` ships the recurring agent shapes so you do not re-derive
them. Each is effects-based and LLM-free where it can be; the domain supplies
the hooks.

| Recipe | Shape |
| --- | --- |
| `fan_out_sources` / `materialize_doc` | retrieval + provenance (above) |
| `Router` | classify into a route with a deterministic fallback |
| `Route` / `RouteCase` | mutually exclusive case groups over one artifact field; expand with `route.groups()` |
| `PlanExecute` | plan → execute → finish, gating each step on its predecessor |
| `ReflectionLoop` | draft → critique → rewrite, with the accept threshold |
| `StatusMachine` | advance an artifact's `status` via a pure `next_status` |
| `ApprovalGate` | human sign-off before finalizing |
| `EphemeralCleanup` / `PrefixedEphemeralCleanup` / `ScopedCleanup` | delete a turn's scratch artifacts, by type or by shared scope |
| `WindowSummarizer` / `RollingDigestSummarizer` | periodic checkpoints / one growing digest (the bounded-window choice) |
| `Conversation` / `Transcript` | multi-turn conversation as typed turns |
| `TurnContext` / `TurnContextStep` | one artifact per turn: bounded history, digest, previous query, follow-up flag |
| `run_tool_loop` | a plain tool-calling loop (no full `LLMAgent`) |
| `find` / `find_all` | pick typed artifacts out of `inputs` |
| `changed_fields` / `downstream_fields` | the "change → rebuild" model |

Import them from `reactifact.recipes`. Deterministic helpers only — no hidden
LLM calls. For multi-turn RAG, `TurnContextStep` is the primitive: downstream
steps consume `TurnContext` instead of taking `Context` and rebuilding history;
use `Unscoped()` on a step-scoped history read when a step needs every turn.

## Where to look

- `references/sources.md` — every source, `SourceRef`, and `fan_out_sources`.
- `docs/en/sources.md`, `docs/en/recipes.md`, `docs/en/knowledge.md`.
- Examples: `examples/knowledge`, `examples/fintech_audit`.
