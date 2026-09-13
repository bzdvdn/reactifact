# Sources

A **Source** is a retrieval capability. Vector search is one strategy, not
*the* strategy — the filesystem, CSV, and the web (`WebSource`) are equally
first-class today, and **embeddings are optional**. The `Source` protocol
(`reactifact/sources.py`) is intentionally small so this list grows by adding
implementations, not by changing the abstraction — direct API, keyword, and
SQL sources are on the roadmap, not yet shipped (see
[roadmap](../roadmap.md#next)); don't reach for them until they land. Sources
live in `Context.resources.sources`.

```python
from reactifact import Context, RuntimeResources
from reactifact.sources import CSVSource, EmbeddingSource, FileSystemSource, WebSource

ctx = Context(
    resources=RuntimeResources(
        sources={
            "docs": FileSystemSource("./docs", embedder=embedder),  # optional
            "catalog": CSVSource("data/price.csv", key="sku", columns=...),
            "web": WebSource(...),
        }
    )
)
```

## SourceRef

The shared result of *every* search is a `SourceRef` — a ranked, scoped pointer:

```python
class SourceRef(BaseModel):
    source_id: str      # which source produced it
    payload: str        # small preview/test snippet
    uid: str            # stable document uid inside the source
    score: float | None # rank/hit score, if the source scores
    query: str | None   # the query that found it
    metadata: dict      # owner_id scoping, extra context
```

`SourceRef.stable_id()` lets you build deterministic artifact ids
(`ref:{stable_id}:{owner_id}` in `fan_out_sources`), so repeated searches are
idempotent.

## The four built-in sources

| Source | Purpose | Notes |
| --- | --- | --- |
| `FileSystemSource` | keyword (and optional embedding) search over local files | `embedder` optional; scores hits |
| `EmbeddingSource` | vector search over an in-memory or prepared corpus | needs an `EmbeddingProvider` |
| `CSVSource` | query catalog/price rows, returns structured rows | deterministic, no embeddings |
| `WebSource` | live web: search + **lazy** page resolution | fetches the *promised* docs on demand |

## Lazy materialization

`WebSource` (and remote sources generally) return *references*, not content.
Resolving the actual document is explicit and lazy — the research demo fetches
only the pages it decides it needs, then links the matched document to the ref:

```text
SourceRef --materialized_from--> TypedDoc
```

The [`materialize_doc` recipe](recipes.md) encodes exactly this flow.

## Typical retrieval stages in a demo

```text
query ──► fan_out_sources ──► SourceRefs (ranked, scoped to owner)
                                   │
                                   ▼  (select relevant + lazy resolve for web)
                              TypedDoc / row payloads
                                   │
                                   ▼
                            Evidence (extracted, scored)
```

## Example: CSVSource for deterministic numbers

Catalog pricing never goes through an LLM. A CSV source returns exact rows; the
estimate stage in the `repair` demo multiplies quantities × unit prices from
the catalog, deterministically (§67):

```python
catalog = CSVSource("data/price.csv", key="name")
rows = await catalog.asearch("штукатурка", limit=20)
```

You can point a `CSVSource` at any structured table and search it by keyword to
get the rows you need for a calculation.