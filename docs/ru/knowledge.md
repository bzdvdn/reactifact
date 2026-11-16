# База знаний (опциональный слой)

`reactifact.knowledge` превращает разнородные документы в индексируемый корпус,
не добавляя второй агентский фреймворк. Слой **code-first**: коннектор — это
объект, конфигурация — обычный Python, а YAML-загрузчика, CLI, реестра плагинов
и type strings нет.

Слой сознательно отделён от реактивного ядра. `reactifact` никогда его не
импортирует; knowledge импортирует core-протоколы и переиспользует эмбеддер,
уже подключённый к `RuntimeResources`:

```text
Connector -> RawDocument -> Document -> Chunk -> EmbeddingProvider -> Index
```

Встроенный пример `examples/knowledge` использует этот слой для текстового
корпуса. На уровне установки это просто core-пакет
(`pip install "reactifact[knowledge]"`); extra не добавляет runtime-зависимостей.

## Source и Connector

Две возможности с разным жизненным циклом:

| | `Source` (`reactifact.sources`) | `Connector` (`reactifact.knowledge`) |
| --- | --- | --- |
| Когда | во время запроса, внутри агента | во время индексации, до запросов |
| Что делает | `search(query)` + `resolve(ref)` | `fetch()` / `list_documents()` |
| Возвращает | `SourceRef` | `RawDocument` |
| Пример | `CSVSource`, `WebSource` | `FilesystemConnector`, ваш API-коннектор |

`IndexSource` — мост: он превращает готовый knowledge-индекс в обычный
`Source` для `RuntimeResources.sources`.
## Собрать пайплайн

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
    embedder=ProviderEmbedder(resources.embedder),  # core-провайдер
    index=SqliteIndex(".reactifact/knowledge.db"),
)

report = await pipeline.run(
    incremental=True,
    state_path=".reactifact/knowledge/state.json",
)
```

`connector` — любой объект с `source_id` и асинхронным `fetch()`, возвращающим
`RawDocument`. Добавьте `list_documents()` и `fetch(uris=...)`, чтобы
инкрементальные запуски не скачивали неизменившиеся тела:

```python
from reactifact.knowledge import DocumentMeta, RawDocument

class DocsConnector:
    source_id = "docs"

    async def list_documents(self) -> list[DocumentMeta]:
        ...

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        ...
```

`RawDocument.from_text(...)` — обычный code-first хелпер. Metadata
(`documentId`, `sourceUrl`, `title`) сливается в каждый чанк, поэтому цитаты
переживают retrieval.

## Запрос

```python
hits = await pipeline.query(
    "how do I install it?",
    top_k=5,
    hybrid=True,          # dense + BM25, слияние через Reciprocal Rank Fusion
    filters={"space": "ENG"},
    principals=["alice"], # опциональная проверка ACL
)
for hit in hits:
    print(hit.source_uri, hit.score, hit.text[:80])
```

`min_score` фильтрует только dense-результаты по косинусу. У hybrid-оценок
другая шкала (rank fusion, не similarity), поэтому косинусный порог отсёк бы
всё — не задавайте `min_score` при `hybrid=True`.

## Rerank

Базовый hook — это доменный код: политика релевантности ваша. `query` даёт
его, а ниже есть опциональный LLM-хелпер:

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

- Вызывается после dense/hybrid retrieval и `min_score`, до финального
  отсечения по `top_k`.
- Получает over-fetch кандидатов (`top_k * 4`), поэтому может продвигать или
  фильтровать.
- Может быть синхронным или асинхронным; возвращает любое подмножество в любом
  порядке.
- Исключения пробрасываются. Это код приложения, и тихий откат к исходному
  порядку скрыл бы проблемы релевантности. Для best-effort retrieval оберните:

```python
async def safe_rerank(query, hits, top_k):
    try:
        return await my_reranker(query, hits, top_k)
    except Exception:
        logger.warning("domain rerank failed", exc_info=True)
        return list(hits[:top_k])
```

### Готовый LLM-реранкер

`llm_reranker` — опциональная фабрика, а не отдельный клиент: она использует
core chat-провайдер `LLMProvider`, независимый от embedding-провайдера, по
которому построен индекс.

```python
from reactifact.knowledge import llm_reranker

rerank = llm_reranker(
    resources.llm,  # chat-провайдер, не resources.embedder
    instructions="Prefer runbooks; ignore deprecated docs.",
)
hits = await pipeline.query("checkout is timing out", top_k=5, rerank=rerank)
```

Доменный промпт можно заменить на обоих уровнях:

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

- `system_prompt` заменяет системный промпт целиком — сохраните в нём
  `RERANK_OUTPUT_CONTRACT`.
- `instructions` добавляет доменные правила к базовому промпту.
- `user_prompt` заменяет рендер нумерованных excerpt'ов и получает полные
  `SearchHit`, поэтому может использовать metadata (`kind`, `documentId`, ...).
- Fail-soft: ошибка провайдера, невалидный JSON или не-перестановка вызывают
  `on_error` (или warning в лог) и сохраняют исходный порядок. Сырой
  `rerank=` hook остаётся fail-loud.
- Сначала hybrid: LLM-реранк добавляет latency, стоимость и
  недетерминированность.

## Индексы

| Индекс | Хранение | Особенности |
| --- | --- | --- |
| `MemoryIndex` | процесс | тесты, небольшие прототипы; поддерживает keyword-поиск |
| `SqliteIndex` | один файл | zero infrastructure, brute-force косинус; поддерживает keyword-поиск |
| `QdrantIndex` | удалённо | минимальный REST-клиент (без SDK); коллекция создаётся по требованию; hybrid сужает выдачу на сервере, затем BM25 ранжирует локально |
| `PgVectorIndex` | Postgres | extra `pg`; нужен `CREATE EXTENSION vector`; косинус; серверные metadata/ACL-фильтры и keyword-поиск через `tsvector` |
| `ChromaIndex` | локальная папка / удалённый клиент | опциональный extra `chromadb`; косинусное расстояние; только dense (hybrid деградирует до dense); `client=` переиспользует существующий Chroma-клиент |

Все бэкенды реализуют детерминированные point id, per-source `reconcile`,
фильтры по metadata и опциональную видимость по ACL. Смена бэкенда не меняет
пайплайн.

`PgVectorIndex` требует `pip install "reactifact[pg]"` и базу с расширением
pgvector. Передайте `dsn=` или `dsn_env=`; `text_search_config` по умолчанию
`simple` (языконейтрально), можно `english` для стемминга. `ChromaIndex`
опционален: `pip install "reactifact[knowledge-chroma]"`. Значения metadata
строкифицируются под scalar-only ограничение Chroma; полные исходные metadata
сохраняются в `__metadata` и возвращаются в `SearchHit.metadata`.

## Инкрементальная индексация

`run(incremental=True)` хранит состояние `DocStore`, ключом которого служат
**идентичность источника и подпись пайплайна** (настройки extraction, chunking,
embedding). Изменение размера чанка или модели эмбеддингов инвалидирует
отпечатки, а не оставляет устаревшие чанки. Состояние коммитится только после
успешного запуска.

Ошибка коннектора прерывает запуск **до** reconcile, поэтому временная
недоступность источника никогда не выглядит как удаление корпуса.

## Мост в агентский рантайм

Две интеграции явные и опциональные:

```python
from reactifact.knowledge import IndexSource, IngestStep

# retrieval как обычный reactifact Source
source = IndexSource(pipeline, hybrid=True, min_score=0.15)
ctx = Context(resources=RuntimeResources(embedder=..., sources={"kb": source}))

# индексация как шаг (когда ingestion — часть реактивного потока)
step = IngestStep(pipeline)
```

В большинстве сервисов индексация — фоновая задача, а не шаг агента: вызывайте
`pipeline.run(...)` из scheduler/worker и отдавайте агентам только `IndexSource`.

## Свои коннекторы и индексы

Встроенный `FilesystemConnector` закрывает обычный случай локальных документов:

```python
from reactifact.knowledge import FilesystemConnector, KnowledgePipeline

pipeline = KnowledgePipeline(
    FilesystemConnector("docs", source_id="docs"),
    embedder=embedder,
    index=index,
)
```

Он даёт content-отпечатки и `fetch(uris=...)`, поэтому инкрементальные запуски
пропускают неизменившиеся файлы. Отсутствующий root поднимает `SourceError`,
а не трактует корпус как пустой и не удаляет его при reconcile.

Реестра расширений нет — реализуйте протокол и передайте экземпляр:

- **Коннекторы источников** реализуют `Connector` (`source_id` + `fetch()`) или
  `ListingConnector` (плюс `list_documents()` и `fetch(uris=...)` для дешёвых
  инкрементальных пропусков).
- **Векторные хранилища** реализуют `Indexer` (`upsert`, `reconcile`,
  `search`); добавьте `KeywordIndexer.keyword_search` для hybrid-поиска и
  `BatchIndexer.upsert_batch` для пакетной записи.

`MemoryIndex`, `SqliteIndex`, `QdrantIndex`, `PgVectorIndex` и `ChromaIndex` —
референсные реализации одного контракта; адаптер под Milvus — это просто ещё
один класс, переданный как `index=`.

## Страница статуса (опциональный FastAPI router)

Индексация — фоновая задача, а не агентский запуск, поэтому она не попадает в
`RunTrace`. У knowledge-слоя есть отдельный read-only router, который читает
ledger запусков из `DocStore`:

```python
from fastapi import FastAPI
from reactifact.knowledge.web import create_knowledge_router

app = FastAPI()
app.include_router(
    create_knowledge_router(docstore, index=pipeline.index),
    prefix="/knowledge",
)
```

- `GET /knowledge/` — последний запуск по каждому источнику, счётчики
  документов/чанков, ошибки, embed-cache hits и история последних запусков.
- `GET /knowledge/api/knowledge/status` — те же данные в JSON.
- `index` опционален; если передан, страница показывает тип бэкенда и
  `count()`.

Нужен extra `web` (`pip install "reactifact[web]"`); монтируется независимо от
traces-router. Дашборд traces по-прежнему описывает агентские запуски, а
`/knowledge` — сборку корпуса.

## Чего здесь сознательно нет

- YAML-пайплайнов и CLI `knowledge`.
- Реестра плагинов и type strings.
- Синтеза ответов, реранка и чата — используйте `reactifact.quick.rag`,
  `reactifact.providers` и `reactifact.recipes`.
- Evaluation и tracing — используйте `reactifact.eval` и `reactifact.tracing`.

Именно благодаря этому `knowledge` остаётся слоем возможностей, а не вторым
фреймворком внутри reactifact.
