# Migrating to reactifact 0.24.0

0.24 adds a lot, but it also finishes the hard breaks planned for this line:
the old `agents` names are gone, core LLM helpers take a provider instead of a
context, and the optional knowledge layer tightens its contracts. There is no
compatibility shim for the items below.

## The 0.x `agents` names are removed

`Agent`, `create_agent`, `validate_agents`, the `agents=` keyword and the
`reactifact.agents` module are gone. `Runtime.agents` is now `Runtime.groups`.

Before:

```python
from reactifact import Agent, Runtime, create_agent

group = create_agent("qa", steps=[answer])
runtime = Runtime(context, agents=[group])
```

After:

```python
from reactifact import Runtime, create_step_group

group = create_step_group("qa", steps=[answer])
runtime = Runtime(context, groups=[group])
```

The same keyword change applies to `ChatAssistant`, `run_subflow`, `swarm` and
`quick.chat_agent`. `validate_agents(...)` becomes
`validate_contracts(groups=...)`.

## `Artifact[T] | None` now means "handle or `None`"

An optional artifact parameter used to fall through to the data path and return
the model. It now returns the `Artifact[T]` handle (or `None`). Use `T | None`
when you want optional data; `Artifact[T]` and `list[Artifact[T]]` still return
handles.

## Optional provider parameters resolve from resources

A step parameter typed `LLMProvider | None` (and other optional services) now
receives the registered provider instead of silently staying `None`. If `None`
was your "no provider, use a fallback" signal, check `resources.has(...)` or
make the parameter required.

## Pending HITL replies are `waiting: true`

When a `PendingQuestion` exists, `Chat` returns a structured `question` payload
with `waiting: true` instead of an ordinary reply. Branch on `waiting` rather
than assuming every reply has `reply`; `Chat(resume_pending=True)` turns the
next user message into the answer.

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
`OnDelete`, `OnStale`) directly. It had no separate deprecation release; 0.24
is a deliberate exception to the one-minor window.

## Additive highlights

- Lifecycle consume classes: `OnCreate`, `OnUpdate`, `OnDelete`, `OnStale`.
- `Progress` collaborator for UI statuses without `Context`.
- Owned resources (`RuntimeResources.register(..., owned=True)`).
- Pydantic resources and implicit read-only inputs.
- Optional artifact handles and the `Ensure` primitive.
- `SqliteDocStore`, vector-dimension validation and runtime-checkable index
  capabilities.
- `HttpConnector`/`S3Connector`, CSV/JSON/PDF/DOCX extractors and batched
  ingestion.
- MMR diversity, section/document expansion and retrieval metrics.
