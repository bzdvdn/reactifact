# Sources reference

A `Source` abstracts "where content lives". The agent never knows the search
mechanics; the source decides (keywords, vectors, SQL, HTTP).

```python
# not-run: illustrative
from reactifact.sources import FileSystemSource, CSVSource, EmbeddingSource, WebSource

resources = RuntimeResources(sources={
    "docs": FileSystemSource("knowledge/docs", source_id="docs", scorer=keyword_score),
    "costs": CSVSource("knowledge/costs", source_id="costs"),
    "rag": EmbeddingSource("knowledge/docs", embedder=embedder),
    "web": WebSource(urls=["https://example.com/page"]),
})
```

## The interface

```python
# not-run: illustrative
class Source(ABC):
    preferred: bool = False            # polled first when true (e.g. vector RAG)
    def search(self, query: str, limit: int = 10) -> list[SourceRef]: ...
    async def asearch(self, query, limit=10) -> list[SourceRef]: ...   # default: search()
    async def resolve(self, ref: SourceRef) -> Any: ...
```

Sources without search simply return `[]` and stay usable via `resolve`.
`asearch` is the aggregator's entry point (vector sources cannot embed the query
synchronously).

## Built-in sources

| Source | Constructor | `resolve` returns |
| --- | --- | --- |
| `FileSystemSource` | `(root, source_id="filesystem", extensions=(".md", ".txt"), scorer=...)` | file text |
| `CSVSource` | `(root, source_id="csv", extensions=(".csv",), scorer=...)` | a row `dict` |
| `EmbeddingSource` | `(root, source_id="rag", embedder=..., chunk_size=1200, score_threshold=0.15)` | chunk text (async search only) |
| `WebSource` | `(urls=[...], source_id="web", timeout=15.0, transport=...)` | page text (lazy fetch) |

`scorer(text, query) -> float` defaults to token overlap; pass
`recipes.keyword_score` (with `use_stems=True` for Russian) for better matching.
`EmbeddingSource` builds its index lazily on first search; call
`invalidate()` after files change. `WebSource` fetches lazily and accepts an
injectable `transport` (e.g. httpx `MockTransport`) for hermetic tests.

Access a source by id with `context.resources.get_source(source_id)`.

## SourceRef

```python
# not-run: illustrative
SourceRef(
    source_id="docs",
    locator="guide.md",          # path / URL / row index / chunk id
    score=0.82,
    title="Guide",
    excerpt="...",
    query_id="q1",
    metadata={"owner_id": "user-1"},
)
```

`stable_id()` hashes `source_id:locator` — the basis for idempotent creation.
A `SourceRef` is an artifact: you can link answers to it and query it later.

## fan_out_sources

```python
# not-run: illustrative
refs = await fan_out_sources(
    context,
    query,
    owner_id="user-1",
    limit=5,
    query_id=None,               # defaults to owner_id
    extra_metadata=None,
    on_start=lambda sid: ...,    # progress: source starting
    on_count=lambda sid, n: ..., # progress: hits found
)
```

Polls every source (preferred first), ranks by score, keeps the top `limit`, and
creates each as an idempotent artifact `id=f"ref:{stable_id}:{owner_id}"`. Must
run inside a produce (it writes into the current effects). Returns the scoped
refs so the caller can inspect them or add a marker.

## materialize_doc

```python
# not-run: illustrative
doc = await materialize_doc(
    context,
    ref_artifact,
    doc_factory,                 # (context, ref_artifact, content) -> BaseModel
    relation="materialized_from",
)
```

Resolves a ref (lazily), builds a domain document, creates it under a stable id
`resolved:{ref.id}`, and links it `relation → SourceRef`. Returns `None` when
the source is missing or resolve fails.
