# Migrating to reactifact 0.24.0

0.24 is mostly additive, but the optional knowledge layer gets a few deliberate
hard breaks, and core LLM helpers now take a provider instead of a context.

## LLM helpers

Before:

```python
from reactifact.structured import structured_llm

body = await structured_llm(context, schema=Answer, user=text)
```

After — use the injected provider (the usual case inside a step):

```python
@step(consumes=Question)
async def answer(question: Question, llm: LLMProvider) -> Answer:
    return await llm.structured(Answer, user=question.text)
```

Or pass the run's provider explicitly when you only have a `Context`:

```python
body = await structured_llm(context.resources.llm, schema=Answer, user=text)
```

The same change applies to:

- `json_schema_llm` / `llm.structured_json(...)`;
- `llm_reply` / `llm.reply(...)`;
- `chat_complete` / `chat_complete_full` / `llm.chat(...)`;
- `StructuredLLM.call(llm, user)`.

`llm` may be `None`; the helpers keep their honest `None` /
`on_error("no_provider")` fallback. The old `context=` keyword is removed — pass
the provider directly (a clear `TypeError` is better than silently accepting a
`Context` in a provider slot).

## Knowledge point IDs include the chunk index

`point_id(source_uri, text)` now also hashes the chunker's `chunk_index` when it
is present. Two identical section bodies in one document no longer collapse into
a single point.

Effect: every existing knowledge index gets new point IDs. Run one full
(non-incremental) reindex after upgrading — `pipeline.run()` without
`incremental=True`, or clear the index first. This is a storage migration, not a
schema change; no data is lost.

## `DocStore` is a protocol

`DocStore` now names the backend contract; instantiate `JsonDocStore` (the
default) or `SqliteDocStore`:

```python
from reactifact.knowledge import JsonDocStore, SqliteDocStore

json_store = JsonDocStore(".reactifact/knowledge/state.json")
sqlite_store = SqliteDocStore(".reactifact/knowledge/state.sqlite3")
```

`pipeline.run(state_path=...)` still uses `JsonDocStore`; pass `docstore=` to
choose SQLite. Both expose the same `entry`/`commit`/`save`/`record_run`/
`runs`/`snapshot` surface.

## `MemoryIndex.count` is async

`index.count` (property) is now `await index.count()`, matching `SqliteIndex`,
`QdrantIndex`, `PgVectorIndex` and `ChromaIndex`. The status router checks the
`CountableIndex` protocol.

## Hybrid retrieval fails loudly

Two silent degradations became `ConfigError`:

- `min_score` with `hybrid=True` — RRF scores are ranks, not similarities;
- `hybrid=True` on an index without `keyword_search` — use `hybrid=False`
  (`ChromaIndex` is dense-only).

## Hit diagnostics and `SourceRef.fragment`

`SearchHit` gained `dense_rank`, `keyword_rank`, `rerank_rank`, `score_kind` and
`chunk_id` (all additive). `SourceRef` gained `fragment`; knowledge refs use the
chunk id as their fragment, so chunks of one document now get distinct artifact
ids. Stored refs without a fragment keep their old ids (`fragment` defaults to
`""`).

## `consume()` removed

The lowercase `consume(...)` factory duplicated `Consume(...)` and is gone.
Use `Consume(Type, ...)` or a lifecycle subclass (`OnCreate`, `OnUpdate`,
`OnDelete`, `OnStale`) directly.

## Additive highlights

- `Progress` collaborator for UI statuses without `Context`.
- Owned resources (`RuntimeResources.register(..., owned=True)`).
- Pydantic resources and implicit read-only inputs.
- Optional artifact handles and the `Ensure` primitive.
- `SqliteDocStore`, vector-dimension validation and runtime-checkable index
  capabilities.
- `HttpConnector`/`S3Connector`, CSV/JSON/PDF/DOCX extractors and batched
  ingestion.
- MMR diversity, section/document expansion and retrieval metrics.
