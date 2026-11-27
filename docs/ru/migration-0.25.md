# Переход на reactifact 0.25.0

0.25 — в основном аддитивная эргономика. Ломающих изменений только два, оба
запланированные; если вы не используете `EmbeddingSource` и старые shim-модули
инструментов, менять код не нужно.

## `EmbeddingSource` удалён

В core больше нет векторного `Source`. Соберите retrieval через опциональный
knowledge-слой и откройте его как обычный источник через `IndexSource`.

Было:

```python
from reactifact.sources import EmbeddingSource

source = EmbeddingSource("./docs", embedder=embedder)
```

Стало:

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

`IndexSource` сохраняет контракт `Source` (`asearch`/`resolve`) и добавляет
hybrid-поиск, MMR, reranking и ACL-фильтрацию. Если нужен собственный векторный
store, реализуйте `Source.asearch` сами; `FileSystemSource`, `CSVSource` и
`WebSource` остаются в core.

У этого удаления не было отдельного deprecation-релиза: 0.25 — осознанное
исключение из правила одного минорного окна, потому что knowledge-слой теперь
закрывает этот сценарий целиком.

## Shim-модули инструментов удалены

`reactifact.tool_use`, `reactifact.native_tool_use` и
`reactifact.agent_tool` завершили окно совместимости 0.20–0.24 и удалены.
Используйте канонические модули:

| Удалено | Используйте |
| --- | --- |
| `reactifact.tool_use` | `reactifact.tools.use` |
| `reactifact.native_tool_use` | `reactifact.tools.native` |
| `reactifact.agent_tool` | `reactifact.tools.agent` |

Сохранённые type id, которые всё ещё называют удалённый модуль, резолвятся
через `reactifact.types.resolve`, поэтому старые сессии продолжают загружаться.
