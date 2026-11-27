# Migrating to reactifact 0.25.0

0.25 is mostly additive ergonomics. Two planned removals are the only breaking
changes; if you do not use `EmbeddingSource` or the old tool-module shims, no
code changes are needed.

## `EmbeddingSource` is removed

The core no longer ships a vector `Source`. Build retrieval with the optional
knowledge layer and expose it through `IndexSource`.

Before:

```python
from reactifact.sources import EmbeddingSource

source = EmbeddingSource("./docs", embedder=embedder)
```

After:

```python
from reactifact.knowledge import (
    FilesystemConnector,
    IndexSource,
    KnowledgePipeline,
)

pipeline = KnowledgePipeline(
    FilesystemConnector("./docs", source_id="docs"),
    embedder=embedder,
    index=index,
)
source = IndexSource(pipeline, hybrid=True)
```

`IndexSource` keeps the regular `Source` contract (`asearch`/`resolve`) and adds
hybrid search, MMR diversity, reranking and ACL filtering. To keep a bespoke
vector store, implement `Source.asearch` yourself; `FileSystemSource`,
`CSVSource` and `WebSource` remain in the core.

This removal had no separate deprecation release: 0.25 is a deliberate
exception to the one-minor window, because the knowledge layer now covers the
case end to end.

## Tool-module shims are removed

`reactifact.tool_use`, `reactifact.native_tool_use` and
`reactifact.agent_tool` completed their 0.20–0.24 compatibility window and are
gone. Use the canonical modules:

| Removed | Use |
| --- | --- |
| `reactifact.tool_use` | `reactifact.tools.use` |
| `reactifact.native_tool_use` | `reactifact.tools.native` |
| `reactifact.agent_tool` | `reactifact.tools.agent` |

Persisted type ids that still name a removed module resolve through
`reactifact.types.resolve`, so stored sessions keep loading.
