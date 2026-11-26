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
переживают retrieval. Встроенные чанкеры также добавляют координаты
`section_index`, `chunk_index` и `heading` — этого достаточно, чтобы отрисовать
и подсветить цитату.

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

Retrieval ограничен `source_id` пайплайна: в общем индексе чужие источники не
попадут в выдачу. Передайте `source_id=`, чтобы явно запросить другой источник,
или голый индекс + `embedder=` в `reactifact.knowledge.query(...)`.

`min_score` фильтрует только dense-результаты по косинусу. У hybrid-оценок
другая шкала (rank fusion, не similarity), поэтому `min_score` при
`hybrid=True` теперь даёт `ConfigError`, а не молча выкидывает всё.
`hybrid=True` также требует индекс с `keyword_search`; dense-only индексы
должны использовать `hybrid=False`.

`max_per_document=N` оставляет не больше `N` чанков одного документа в выдаче,
чтобы один длинный документ не вытеснял остальные. Документы определяются по
metadata `document_id`/`documentId`, иначе по `source_uri`. Ограничение
применяется до `rerank`, поэтому реранкер видит разнообразных кандидатов.

`mmr=MMR(lambda_=0.3)` переранжирует over-fetched кандидатов по релевантности
**и** новизне (Maximal Marginal Relevance). `lambda_=1` — чистая
релевантность, `0` — чистая новизна; пайплайн эмбеддит тексты кандидатов своим
эмбеддером, после `min_score`/`max_per_document` и до `rerank`. Для
`query_by_vector` при MMR нужен `embedder=`.

Каждый хит несёт provenance retrieval: `dense_rank`, `keyword_rank`,
`rerank_rank`, `score_kind` (`"dense"` или `"rrf"`) и стабильный `chunk_id`.
`IndexSource` кладёт их в `SourceRef.metadata["retrieval"]` и использует chunk
id как `fragment` рефа, поэтому два чанка одного документа получают разные id
артефактов.

Вектор уже есть? `query_by_vector(...)` делает тот же retrieval без эмбеддинга
текста. Передайте `query_text=`, если используете `hybrid=True` (BM25 нужны
слова) или `rerank=` (реранкеру нужен запрос).

## Метрики retrieval

`reactifact.eval.retrieval` оценивает качество ранжирования без ответа:
recall@k, precision@k, MRR, nDCG@k и hit rate. `reactifact.knowledge.eval`
связывает пайплайн с этими метриками:

```python
from reactifact.eval import RetrievalCase
from reactifact.knowledge import evaluate_pipeline

report = await evaluate_pipeline(
    pipeline,
    [
        RetrievalCase("how do I install?", relevant=["install.md"]),
        RetrievalCase("refund policy", relevant={"refund.md": 2.0}),
    ],
    k=5,
    hybrid=True,
)
print(report.render())
report.assert_passed({"recall": 0.9})   # CI-гейт
```

`relevant` — список (бинарная) или mapping (graded gains). Ключ по умолчанию
сравнивает документы (`documentId`, иначе `source_uri`); передайте
`key=chunk_key` для метрик на уровне чанков. `cases_from_dataset(dataset)`
строит кейсы из `inputs["query"]` и `reference_outputs["relevant"]`. Кейс без
релевантных id пропускается, а не получает ноль.

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
| `ChromaIndex` | локальная папка / удалённый клиент | опциональный extra `chromadb`; косинусное расстояние; только dense (`hybrid=True` даёт `ConfigError`); `client=` переиспользует существующий Chroma-клиент |

Все бэкенды реализуют детерминированные point id, per-source `reconcile`,
фильтры по metadata и опциональную видимость по ACL. Point id включает
`chunk_index` чанкера, поэтому одинаковые тела в двух секциях не схлопываются. Смена бэкенда не меняет
пайплайн.

Встроенные индексы проверяют размерность вектора: первый upsert фиксирует
размер, а вектор другого размера даёт `ConfigError` до записи. Смена модели
эмбеддингов падает на границе, а не внутри клиента БД. `ChromaIndex`
дополнительно полагается на собственную проверку коллекции для уже
существующих данных.

Опциональные возможности — это runtime-checkable протоколы, а не `getattr`:
`KeywordIndexer` (`keyword_search`), `BatchIndexer` (`upsert_batch`) и
`CountableIndex` (`count`). Адаптер реализует только то, что поддерживает;
пайплайн проверяет протокол и использует возможность либо даёт явный
`ConfigError`.

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
отпечатки, а не оставляет устаревшие чанки. Хэш документа включает content type
и metadata, а не только байты: изменение ACL/title/tag переиндексирует
документ даже при неизменном тексте. Состояние коммитится только после
успешного запуска. Запуски сериализуются по `(state_path, source_id)` внутри
процесса; между процессами контракт — один ingestion-писатель на источник
(если запускаете несколько воркеров, используйте внешнюю блокировку).

Ошибка коннектора прерывает запуск **до** reconcile, поэтому временная
недоступность источника никогда не выглядит как удаление корпуса.

### Бэкенды DocStore

`DocStore` — это протокол. `JsonDocStore` (по умолчанию при `state_path=`) —
один JSON-файл, перезаписываемый при сохранении; подходит для небольших
корпусов. `SqliteDocStore` хранит те же fingerprints и ledger запусков в
SQLite с транзакционной построчной записью:

```python
from reactifact.knowledge import SqliteDocStore

docstore = SqliteDocStore(".reactifact/knowledge/state.sqlite3")
await pipeline.run(docstore=docstore)
```

Оба дают `entry`/`commit`/`save`/`record_run`/`runs`/`snapshot`, поэтому
пайплайну и странице статуса не важно, какой бэкенд используется. Чтобы
выбрать SQLite, передайте `docstore=` явно; `state_path=` продолжает
использовать JSON.

### Изоляция ошибок, прогресс и конкурентность

По умолчанию плохой документ прерывает запуск (`on_error="fail_fast"`). Для
больших корпусов можно собирать ошибки и продолжать:

```python
from reactifact.knowledge import IngestProgress

events: list[IngestProgress] = []
report = await pipeline.run(
    incremental=True,
    on_error="collect",
    on_progress=events.append,
)
report.documents_failed       # количество
report.failures               # [IngestFailure(uri, stage, error), ...]
```

Документ с ошибкой **не коммитится** в `DocStore`; его старые точки в индексе
сохраняются, а следующий incremental-запуск повторит попытку. `on_progress`
получает этапы (`start`, `fetch`, `extract`, `embed`, `index`, `reconcile`,
`done`) и события по документам/ошибкам со счётчиками.

`document_concurrency` (по умолчанию `1`) параллелит extract/chunk по
документам; события прогресса приходят по мере завершения каждого документа.
Fetch остаётся за коннектором; `ProviderEmbedder` уже батчит и ограничивает
конкурентность эмбеддингов.

`run(..., batch_size=N)` ограничивает память ingestion: с `ListingConnector`
за раз скачивается, извлекается, эмбеддится и пишется только один батч тел, а
pending-чанки и векторы освобождаются после каждого батча. State всё равно
коммитится один раз в конце; ошибка fail-fast оставляет предыдущие батчи
записанными, но без reconcile — следующий запуск это исправит.

Без `DocStore` (неинкрементальный запуск) у упавшего документа нет прежнего
хэша для восстановления, поэтому `on_error="collect"` пропускает reconcile
удалений за этот запуск: успешные документы всё равно upsert-ятся, но ничего
не удаляется. Для полного поведения «сохранить и повторить» используйте
`incremental=True`.

## Мост в агентский рантайм

Две интеграции явные и опциональные:

```python
from reactifact.knowledge import IndexSource, IngestStep

# retrieval как обычный reactifact Source
source = IndexSource(pipeline, hybrid=True)
ctx = Context(resources=RuntimeResources(embedder=..., sources={"kb": source}))

# индексация как шаг (когда ingestion — часть реактивного потока)
step = IngestStep(pipeline)  # возвращает IngestReportArtifact
```

В большинстве сервисов индексация — фоновая задача, а не шаг агента: вызывайте
`pipeline.run(...)` из scheduler/worker и отдавайте агентам только `IndexSource`.

`IndexSource(expand="section")` (или `"document"`) заставляет `resolve`
возвращать родительскую секцию/документ вместо найденного чанка: он
перезапрашивает документ и прогоняет тот же экстрактор, поэтому parent-текст
никогда не хранится в индексе. Для `ListingConnector` используется
`fetch(uris=...)`, для обычного коннектора — полный fetch; отсутствующий
документ или секция даёт `KnowledgeError`, который `materialize_doc` передаёт
в `on_error`.

`IndexSource(principals=["alice"])` (или callable, возвращающий principals)
применяет ACL-фильтрацию к каждому поиску, чтобы RAG не мог молча запросить
без identity. `require_principals=True` превращает отсутствие identity в
`ConfigError`, а не в нефильтрованный поиск.

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

### Встроенные коннекторы и экстракторы

| Коннектор | Особенности |
| --- | --- |
| `FilesystemConnector` | текстовые файлы в каталоге; content-хэши; fetch по URI |
| `HttpConnector` | явные URL или sitemap; отпечатки `ETag`/`Last-Modified`; `httpx` — core-зависимость |

| Экстрактор | Вход | Extra |
| --- | --- | --- |
| `MarkdownExtractor` | Markdown / plain text | — |
| `HtmlExtractor` | статический HTML | — |
| `CsvExtractor` | CSV → секции Markdown-таблиц | — |
| `JsonExtractor` | JSON-объекты/массивы → секции | — |
| `PdfExtractor` | одна секция на страницу PDF | `knowledge-pdf` (`pypdf`) |
| `DocxExtractor` | заголовки, абзацы и таблицы DOCX | `knowledge-office` (`python-docx`) |

`AutoExtractor` выбирает по content type, затем по суффиксу URI. Опциональные
экстракторы импортируются лениво, а ошибка называет нужный extra.

```python
from reactifact.knowledge import HttpConnector

pipeline = KnowledgePipeline(
    HttpConnector(sitemap="https://docs.example/sitemap.xml"),
    embedder=embedder,
    index=index,
)
```

Реестра расширений нет — реализуйте протокол и передайте экземпляр:

- **Коннекторы источников** реализуют `Connector` (`source_id` + `fetch()`) или
  `ListingConnector` (плюс `list_documents()` и `fetch(uris=...)` для дешёвых
  инкрементальных пропусков).
- **Векторные хранилища** реализуют `Indexer` (`upsert`, `reconcile`,
  `search`); добавьте `KeywordIndexer.keyword_search` для hybrid-поиска,
  `BatchIndexer.upsert_batch` для пакетной записи и `CountableIndex.count` для
  страницы статуса. Это runtime-checkable протоколы: достаточно реализовать
  метод.

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
