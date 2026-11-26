# Переход на reactifact 0.24.0

0.24 в основном аддитивна, но опциональный knowledge-слой получил несколько
намеренных жёстких переломов, а core LLM-хелперы теперь принимают провайдера,
а не `Context`.

## LLM-хелперы

Было:

```python
from reactifact.structured import structured_llm

body = await structured_llm(context, schema=Answer, user=text)
```

Стало — используйте инжектированный провайдер (обычный случай в шаге):

```python
@step(consumes=Question)
async def answer(question: Question, llm: LLMProvider) -> Answer:
    return await llm.structured(Answer, user=question.text)
```

Или передайте провайдера явно, если есть только `Context`:

```python
body = await structured_llm(context.resources.llm, schema=Answer, user=text)
```

То же касается:

- `json_schema_llm` / `llm.structured_json(...)`;
- `llm_reply` / `llm.reply(...)`;
- `chat_complete` / `chat_complete_full` / `llm.chat(...)`;
- `StructuredLLM.call(llm, user)`.

`llm` может быть `None`; хелперы сохраняют честный `None` /
`on_error("no_provider")`. Старый keyword `context=` удалён — передавайте
провайдера напрямую.

## Point ID knowledge-слоя включают индекс чанка

`point_id(source_uri, text)` теперь учитывает `chunk_index` чанкера, если он
есть. Два одинаковых тела секций в одном документе больше не схлопываются в
одну точку.

Следствие: у всех существующих knowledge-индексов меняются point ID. После
обновления выполните один полный (неинкрементальный) reindex —
`pipeline.run()` без `incremental=True` или очистите индекс. Это миграция
хранилища, не схемы; данные не теряются.

## `DocStore` — протокол

`DocStore` теперь называет контракт бэкенда; создавайте `JsonDocStore` (дефолт)
или `SqliteDocStore`:

```python
from reactifact.knowledge import JsonDocStore, SqliteDocStore

json_store = JsonDocStore(".reactifact/knowledge/state.json")
sqlite_store = SqliteDocStore(".reactifact/knowledge/state.sqlite3")
```

`pipeline.run(state_path=...)` по-прежнему использует `JsonDocStore`; передайте
`docstore=`, чтобы выбрать SQLite. Оба дают одинаковый набор
`entry`/`commit`/`save`/`record_run`/`runs`/`snapshot`.

## `MemoryIndex.count` — async

`index.count` (property) стал `await index.count()`, как у `SqliteIndex`,
`QdrantIndex`, `PgVectorIndex` и `ChromaIndex`. Статус-страница проверяет
протокол `CountableIndex`.

## Hybrid retrieval падает явно

Две тихие деградации стали `ConfigError`:

- `min_score` при `hybrid=True` — RRF-оценки это ранги, а не similarity;
- `hybrid=True` на индексе без `keyword_search` — используйте `hybrid=False`
  (`ChromaIndex` — dense-only).

## Диагностика хитов и `SourceRef.fragment`

`SearchHit` получил `dense_rank`, `keyword_rank`, `rerank_rank`, `score_kind` и
`chunk_id` (всё аддитивно). `SourceRef` получил `fragment`; knowledge-рефы
используют chunk id как fragment, поэтому чанки одного документа получают
разные id артефактов. Сохранённые рефы без fragment сохраняют старые id
(`fragment` по умолчанию `""`).

## Аддитивные изменения

- `Progress` — UI-статусы без `Context`.
- Owned resources (`RuntimeResources.register(..., owned=True)`).
- Pydantic resources и неявные read-only inputs.
- Опциональные handles артефактов и `Ensure`.
- `SqliteDocStore`, проверка размерности векторов и runtime-checkable
  capability-протоколы.
- `HttpConnector`/`S3Connector`, экстракторы CSV/JSON/PDF/DOCX и батчевый
  ingestion.
- MMR, expansion секций/документов и метрики retrieval.
