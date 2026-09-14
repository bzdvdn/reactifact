# Наблюдаемость

Каждый запуск фиксирует трейс. Трейсы *наблюдаемы по умолчанию*: демо-дашборды
работают офлайн без внешних сервисов, а трейсы дополнительно можно отправлять в
Langfuse или Postgres.

## Что содержит трейс

`RunTrace` — один запуск runtime (до завершения `astream`/`run`):

- **Спаны агентов** (`AgentSpan`) — что делал каждый агент: чтения и записи
  артефактов, суммарные ключ/значение патча, который он произвёл.
- **LLM-вызовы** (`LLMCall`) — промпты, ответы (урезанные), расход токенов,
  задержки.
- **Тайминги и порядок** — вся причинная цепочка запуска по порядку.

`RecordingLLM` оборачивает ваш провайдер бесплатно, поэтому **трекинг LLM-вызовов
не требует кода** — достаточно подключить его при настройке ресурсов.

## Хранение и просмотр

### SQLite-хранилище + веб-дашборд (локально, офлайн)

```python
from reactifact.tracing import TraceStore

store = TraceStore("traces.db")   # SQLite-приёмник; также отдаёт запуски UI
```

Интерфейс стореджа — async (`export`/`query`/`get`); SQLite-ядро выполняется в
рабочем потоке, поэтому один и тот же объект работает и в веб-приложении, и в
обычном синхронном коде.

Дашборд — это FastAPI-роутер, монтируемый на ваше приложение:

```python
from reactifact.tracing.web import create_trace_router

app.include_router(create_trace_router(store), prefix="/traces")
```

Он отдаёт `traces.html` (список запусков, фильтрация) и `run.html` (спаны,
чтения, записи, ввод/вывод LLM, тайминги, плюс две живые Mermaid-диаграммы:
**sequence** запуска и его **граф доказательств** — записанные артефакты с
рёбрами провенанса `patch.link`, §34). Пример `devops` монтирует этот роутер
и служит эталонным UI.

![Список трейсов: фильтр по outcome и session, у каждой строки — длительность и число спанов.](../img/tracer-list.png)

Открытый запуск показывает работу по агентам (число спанов, типы затронутых
артефактов) и полную sequence-диаграмму — здесь HITL ask/resume поток примера
`devops`: `k8s` несколько раз вызывает модель через `artifact_created`-спаны,
затем `render` собирает финальный ответ:

![Детали запуска: работа по агентам плюс живая sequence-диаграмма каждой записи артефакта и вызова LLM.](../img/tracer-run-detail.png)

### Langfuse и Postgres как дополнительные приёмники

Трейс можно отправлять в несколько мест сразу через `CompositeTracer`, передаваемый
в `Runtime`. `Tracer` — тонкий: `on_turn_begin` → `on_span` → `on_turn_end`
(I/O выполняет только `on_turn_end`; приёмники экспортируют асинхронно).

```python
from reactifact import Runtime
from reactifact.tracing import LangfuseTracer, PostgresStore, TraceStore

runtime = Runtime(
    ctx,
    agents=[...],
    tracer=[
        TraceStore("traces.db"),                        # локальный дашборд
        LangfuseTracer(public_key="...", private_key="...",
                       host="https://cloud.langfuse.com"),
        PostgresStore(dsn="postgresql://…"),            # требуется extra pg
    ],
)
```

Трейсы доставляются один раз в конце хода (семантика доставки-один-раз); трейсер
Langfuse мапит суммарные чтения/записи в `input`/`output`, чтобы таймлайн читался
в их UI. SQLite `TraceStore` остаётся одним из источников для веб-дашборда;
Postgres зеркалирует ту же схему `runs`/`spans`.

И `TraceStore`, и `PostgresStore` реализуют `TraceReader` (async `query`/`get`),
поэтому `create_trace_router` работает и с теми, и с другими: укажите дашборду
`PostgresStore(dsn)` — и он покажет трейсы из Postgres без локального
SQLite-файла.

### `OTLPTracer`: любой OTLP/HTTP-коллектор

`LangfuseTracer` привязан к Langfuse (собственное пространство атрибутов
`langfuse.*`). `OTLPTracer` экспортирует тот же запуск как вендор-нейтральные
спаны — только атрибуты GenAI semantic conventions (`gen_ai.*`, плюс
пространство `reactifact.*` для reads/writes/provenance) — в любой
OTLP/HTTP-коллектор: Jaeger, Tempo, Honeycomb, Datadog Agent, локальный
`otel-collector`, ...

```python
from reactifact.tracing import OTLPTracer

runtime = Runtime(
    ctx,
    agents=[...],
    tracer=OTLPTracer(endpoint="http://localhost:4318/v1/traces"),
)
```

Без зависимости от `opentelemetry-sdk` — как и `LangfuseTracer`, он
отправляет OTLP/HTTP JSON-payload напрямую через `httpx` (уже core-зависимость),
поэтому не требует установки дополнительного extra. Передайте `headers=` для
коллектора, требующего авторизацию.

## Модель эмиссии

- Спаны эмитят только **меняющие состояние** агенты (чистый read/verify-produce
  ничего не эмитит — меньше шума).
- Один `Tracer` на `CompositeTracer`: отдавайте один композит нескольким приёмникам.
- `on_turn_end` — единая точка доставки: новый `astream`/`arun` продвигает id
  запуска (повтор считается новым запуском), и трейс каждого хода финализируется
  один раз.

## События прогресса (реактивность UI)

Для анимированных строк «Думаю… / Составляю план… / Считаю смету…» `Produce`
вызывают `context.announce(message, kind=..., **payload)`. Это превращается в
`ProgressEvent` на `EventHub`, который runtime отдаёт в SSE-поток:

```python
async for event in runtime.astream():
    if event.kind == "status":
        yield sse("status", {"message": event.message})
```

`announce` — не лог, а *первоклассный канал UI*, который web-демо потребляют
буквально.