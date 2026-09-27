---
name: reactifact-rag
description: Retrieve external content in reactifact — configure Sources (filesystem, CSV, web, embeddings), fan out and rank with fan_out_sources, materialize documents with provenance, and compose RAG, routing, plan-execute and reflection recipes. Use whenever a reactifact pipeline must search files/documents/the web, do retrieval-augmented generation, resolve citations, or when the user mentions sources, retrieval, RAG, chunking, embeddings or a scout/resolver step.
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
| `CSVSource(root, source_id=..., scorer=...)` | lexical over rows (returns a dict) |
| `EmbeddingSource(root, embedder=...)` | vector/cosine (async only) |
| `WebSource(urls=[...])` | fetch + keyword |

`SourceRef` (an artifact you can save and link) carries `source_id`, `locator`,
`score`, `title`, `query_id`, `metadata`.

## Fan out → materialize (the RAG shape)

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Consume, Context, Produce, Runtime, RuntimeResources, create_agent,
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
            (doc, sum(term in doc.lower() for term in terms))
            for doc in self._docs
        ]
        hits = [(doc, score) for doc, score in scored if score]
        return [
            SourceRef(source_id=self.source_id, locator=str(i), title=doc, score=float(s))
            for i, (doc, s) in enumerate(hits)
        ][:limit]

    async def resolve(self, ref):
        return self._docs[int(ref.locator)]


class Scout(Produce[Note]):
    artifact_type = Note
    also_creates = (SourceRef,)  # fan_out_sources also writes SourceRef artifacts

    async def produce(self, call):
        refs = await fan_out_sources(
            call.context, call.trigger.data.text, owner_id="q1"
        )
        for ref in refs:
            call.effects.create(Note(text=f"{ref.title} (score={ref.score})"))


ctx = Context(
    resources=RuntimeResources(
        sources={"memory": MemorySource(["gpu costs guide", "cpu notes"])}
    )
)
ctx.create(Question(text="gpu costs"))
agent = create_agent("scout", consumes=[Consume(Question)], produces=[Scout()])
asyncio.run(Runtime(ctx, agents=[agent]).arun())

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
from reactifact.recipes import materialize_doc

async def produce(self, call):
    ref = call.trigger                       # a SourceRef artifact
    doc = await materialize_doc(
        call.context, ref, lambda ctx, r, content: Document(text=content)
    )
    if doc is not None:
        call.effects.create(Answer(text=summarize(doc.text))).link(
            "supported_by", ref
        )
```

It creates the document with a stable id and links it `materialized_from →
SourceRef` (configurable relation). A missing source or resolve failure is a
`None`, not a crash.

## Recipes (ready-made patterns)

`reactifact.recipes` ships the recurring agent shapes so you do not re-derive
them. Each is effects-based and LLM-free where it can be; the domain supplies
the hooks.

| Recipe | Shape |
| --- | --- |
| `fan_out_sources` / `materialize_doc` | retrieval + provenance (above) |
| `Router` | classify into a route with a deterministic fallback |
| `PlanExecute` | plan → execute → finish, gating each step on its predecessor |
| `ReflectionLoop` | draft → critique → rewrite, with the accept threshold |
| `StatusMachine` | advance an artifact's `status` via a pure `next_status` |
| `ApprovalGate` | human sign-off before finalizing |
| `EphemeralCleanup` / `PrefixedEphemeralCleanup` | delete a turn's scratch artifacts |
| `WindowSummarizer` / `RollingDigestSummarizer` | bounded conversation memory |
| `Conversation` / `Transcript` | multi-turn conversation as typed turns |
| `run_tool_loop` | a plain tool-calling loop (no full `LLMAgent`) |
| `find` / `find_all` | pick typed artifacts out of `inputs` |
| `changed_fields` / `downstream_fields` | the "change → rebuild" model |

Import them from `reactifact.recipes`. Deterministic helpers only — no hidden
LLM calls.

## Where to look

- `references/sources.md` — every source, `SourceRef`, and `fan_out_sources`.
- `docs/en/sources.md`, `docs/en/recipes.md`.
- Examples: `examples/knowledge`, `examples/research`, `examples/map_reduce`.
