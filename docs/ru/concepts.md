# Концепции

Полное обоснование дизайна — в [constitution.md](../constitution.md) (на английском). Эта
страница — рабочий обзор шести строительных блоков и их взаимодействия.

## 1. Context

`Context` — версионируемое рабочее состояние. Как git, он хранит историю
коммитов, где каждый коммит — результат применения одного или нескольких
**патчей**.

```python
from reactifact import Context, RuntimeResources
from reactifact.sources import FileSystemSource

ctx = Context(
    resources=RuntimeResources(
        sources={"docs": FileSystemSource("./docs")},
    )
)
```

Ключевые возможности:

| Возможность | Назначение |
| --- | --- |
| `create / update / delete` | кодирование намерения (в runtime запускается посредством агентов) |
| `list_artifacts(Model)` | запрос текущего состояния по типу артефакта |
| `view((M1, M2), condition=…)` | запрос по нескольким типам для решений и памяти чата |
| `related(artifact_id, relation)` | проход по графу провенанса |
| `announce(message, kind=…)` | событие прогресса/статуса для UI |
| `diff / rollback / merge` | просмотр или откат истории; слияние веток |

`Context.resources` несёт то, что *не является состоянием*: провайдеры (LLM,
эмбеддер), источники и произвольные ресурсы приложения (каталог цен, папка
изображений). Агенты их читают, но никогда не сохраняют.

## 2. Artifact

`Artifact` — неизменяемый handle `(id, version, data, content_hash, …)`, где
`data` — модель `pydantic`. Артефакты — **полноценные объекты**, а не строковые
блобы. Каждая ревизия живёт в `ArtifactStore` контекста; читать историю через
`Context.artifact_version`/`version_history`/`find_versions`, а
`Artifact.at/history` — compatibility-мосты. Мутация — только `Context.update`
(или effects), не изменение `artifact.data` на месте: identity — это content
hash.

```python
class Evidence(BaseModel):
    query_id: str
    source: str
    text: str
    score: float
```

Правила:

- У каждого артефакта стабильный `id`. Предпочитайте стабильные id случайным
  (`answer:{query_id}`, `ref:{stable_id}:{owner}`) — идемпотентность и связывание
  провенанса становятся тривиальными.
- `query_id` — соглашение об «owner-ключе», когда к одному ходу работы (вопросу,
  исследовательскому повороту) относится много артефактов. На нём построены
  рецепты и примеры.
- Артефакты иммутабельны как *данные*; изменения выражаются патчами, которые
  создают новые версии.

## 3. Effects и Patch

Авторская поверхность — слот **effects** (см. [Контракт produce](effects.md),
§24): produce пишет `create/update/link/ask` и возвращает `None`. Класс-шаг
объявляет `effects: Effects` в `run()`; функция `@step` использует
`call.effects`. Рантайм компилирует набор эффектов в один атомарный
**`Patch`** — скомпилированный транспорт.

```python
from reactifact import Effects

async def run(self, effects: Effects) -> None:
    answer = effects.create(Answer(query_id=qid, text=text), id="answer:q1")
    answer.link("supported_by", evidence_id)
    effects.update(some_artifact, status="answered")
    return None
```

**Скомпилированные операции** (`reactifact.operations`):

| Операция | Смысл |
| --- | --- |
| `Create` | добавить артефакт (опционально со стабильным `id`) |
| `Update` / `update_fields` | новая ревизия артефакта |
| `Delete` | удалить артефакт |
| `Link` | соединить `source →rel→ target` (провенанс) |
| `Unlink` | убрать связь |

`Patch` (контейнер) собирает рантайm и escape-хук `StepGroup.run`; эффекты
сочетаются *внутри* одного produce, поэтому ничего не применяется до компиляции
рантаймом — атомарность структурная (§41).

HITL — это `human.ask(...)` → `PendingQuestion`, ответ через
`human.resume(...)` (см. [patterns](patterns.md)).

## 4. StepGroup (опционально; бывший Agent)

Модель — это шаги. **`Runtime(ctx, steps=[...])`** оборачивает каждый шаг в
неявную группу из одного, так что в обычном случае контейнер не нужен вовсе.
**`StepGroup`** — необязательный контейнер для шагов, которым нужно что-то
общее: один триггер, retry-политика, timeout, priority, лимит параллельности
или один атомарный коммит. `Runtime` принимает `steps=` и `groups=` вместе
(сначала шаги), а `agents=` остаётся deprecated-алиасом для `groups=`.

```python
from reactifact import StepGroup

class RepairFlow(StepGroup):
    name = "repair_flow"
    steps = [
        CollectStage(), PickStage(), PlanStage(),
        EstimateStage(), ApprovalStage(), AssistantStage(),
    ]
```

- `steps` — экземпляры шагов. `consumes` шага питает его типизированные входы и
  решает, какие события могут его запустить; `produces` — разрешённый набор
  Create-типов. Основной output идёт первым, дополнительные create-only типы —
  в том же tuple: `produces = (Primary, Extra)`; `also_creates` — deprecated
  алиас.
- `StepGroup.consumes` остаётся для imperative escape hatch (группа
  переопределяет `run(event, context)`), но декларативный код держит контракты
  на шагах.
- Если `consumes` задан явно, типизированные артефактные параметры, которых там
  нет, автоматически добавляются как read-only входы; `Consume(..., wakes=False)`
  остаётся для явного списка без типизированного параметра. `debounce=True`
  схлопывает несколько событий одного поколения в один запуск;
  `Consume.by_base(Base)` будит и на наследниках. `JoinConsume`/
  `AbsentConsume` коррелируют типы — см. [patterns](patterns.md).
- Runtime будит группы по событиям, соблюдая бюджет и параллельность.

`StepGroup.run(event, context) -> Patch | None` остаётся низкоуровневым escape
hatch для ручной сборки `Patch`; это не третий повседневный стиль.
`StepGroup` и `create_agent` — deprecated-алиасы для кода на 0.x.

## 5. Step

`Step` — место, где происходит работа. Класс-шаг объявляет
`consumes`/`produces` и получает типизированные входы плюс `effects: Effects` в
`run()`:

```python
from reactifact import Artifact, Effects, Progress, Step

class EstimateStage(Step):
    consumes = (Project,)
    produces = (Project,)

    async def run(
        self,
        project_art: Artifact[Project],
        effects: Effects,
        progress: Progress,
    ) -> None:
        ...
        progress("Считаю смету…")
        effects.update(project_art, stage="estimate")
```

`progress` опционален: он публикует эфемерный UI-статус (`ProgressEvent`) и не
требует инжектить весь `Context`. Используйте его, когда шаг только объявляет
статус, а дальше работает с LLM, инструментами или доменной логикой. Тот же
резолвер инжектит и другие не-модельные параметры из `RuntimeResources`, в том
числе опциональные: `llm: LLMProvider | None` даёт настроенного провайдера или
честный `None`. Явно зарегистрированный тип выигрывает у артефакта, поэтому
pydantic-настройки (`resources.register(Settings, Settings(...))`) можно
инжектить как ресурс; незарегистрированные pydantic-модели остаются
артефактами.

Когда `consumes` задан явно, это **набор пробуждений** шага, а типизированная
подпись — **набор чтений**. Типизированный артефактный параметр, которого нет в
`consumes`, автоматически добавляется как read-only вход
(`Consume(T, wakes=False)`):

```python
class PickStage(Step):
    consumes = (UserMsg,)          # будит только новое сообщение

    async def run(
        self,
        user: Artifact[UserMsg],
        project: Artifact[Project],  # read-only: собирается, но не будит
    ) -> Any:
        ...
```

`project` по-прежнему собирается, валидируется и попадает в provenance — он
просто никогда не будит шаг сам по себе. Если `consumes` не задан, типизированная
подпись выводит и триггеры, и входы; отдельный read-only вход там помечается
`Annotated[T, Wakes(False)]`. `Latest[T]` остаётся разовым запросом к контексту,
а не объявленным входом.

`T` инжектит модель данных, а `Artifact[T]` — handle (id, версия, для `Edit`/
связей). Добавляйте `| None` к любой форме, если вход может отсутствовать:
`Artifact[T] | None` даёт handle или `None`, а не модель данных.

Функциональная форма принимает один `call: StepCall` и пишет через
`call.effects`:

```python
@step(produces=Answer, consumes=Evidence)
async def answer(call: StepCall) -> Answer:
    return Answer(text=call.trigger.data.text)
```

Возврат модели — create-сахар; `effects.*` — для update, link, HITL и
дополнительных create. `produces` валидирует каждый Create-тип, включая
созданные эффектами. См. [effects](effects.md) и [patterns](patterns.md).

Соглашение: **сначала детерминизм** (§67). Право на запуск решают
`consumes`/`When` шага, а не ручное ребро графа; LLM применяется только к
генеративным задачам и оборачивается схемой + фолбэком.

## 6. Провенанс

Каждый производный артефакт ссылается на то, что его произвело. Runtime
записывает **чтения и записи** автоматически; ваш код добавляет доменные связи
через `patch.link`:

```text
Answer ──supported_by──► Claim ──derived_from──► Evidence ──extracted_from──► Doc
```

Зачем это нужно:

- **Объяснимость** — «покажи источники» — это запрос по связям, а не память LLM.
- **Оценка** — сила ответа = соединение уверенности утверждений и поддержки
  доказательствами.
- **Детерминированный аудит** — каждый трейс содержит чтения/записи каждого
  спана агента.

## Сопутствующие куски

- **`Budget` / `RunOutcome` / `RunStats`** — лимиты на запуски, итерации, время,
  вызовы инструментов и токены/стоимость LLM; при исчерпании runtime
  останавливается и сообщает outcome (`RunStats` несёт тоталы токенов/стоимости;
  для стоимости нужен инжектированный `pricer`).
- **`Event` / `EventType`** — формат «что-то изменилось»; агентов будят события
  создания/обновления артефактов, а announce порождает `status`-события на пути
  к UI. `ARTIFACT_STALE` возникает автоматически, когда учтённая зависимость
  артефакта (§43-44) получает новую версию — подписка явная:
  `Consume(Type, event_types=[EventType.ARTIFACT_STALE])`.
- **`Trigger`** — вторичное условие входа для produce (периодический или таймерый
  запуск), независимое от потребляемых артефактов. `context_condition` —
  форма с двумя артефактами (нужна для join/корреляции); `debounce` — флаг,
  который читает `Runtime`, чтобы схлопнуть повторные события в один запуск.
- **`Session` / `SessionStore`** — долговременная рабочая память чата между
  запросами, поверх KV-чекпойнта (файл или SQLite); `Context.compact(...)`
  ограничивает историю долгоживущей сессии.

## Какой API использовать?

Это лестница, а не новые концепции: начинайте с первой строки и спускайтесь
ниже только когда задача действительно этого требует.

### Авторинг шага

| Что нужно | Что использовать | Заметка |
| --- | --- | --- |
| Одно типизированное преобразование | `@step(produces=..., consumes=...)` | верните модель или используйте `call.effects` для update/link |
| Типизированные входы, несколько эффектов, HITL | класс `Step` с `run(..., effects: Effects)` | полная форма; см. [Effects](effects.md) |
| Ручная сборка операций / императивный агент | `Patch`, `StepGroup.run(event, context)` | escape hatch, не повседневный стиль |

### Планирование

| Что нужно | Что использовать |
| --- | --- |
| Реакция на тип | `Consume(Type)` |
| Условный переход | `Consume.by_status(...)` / `Consume.by_field(...)`, `Annotated[T, When(...)]` |
| Читать без пробуждения / latest / scoped lookup | типизированный параметр, которого нет в явном `consumes` (автоматически), `Annotated[T, Wakes(False)]`, когда `consumes` выводится, `Consume(..., wakes=False)`, `Latest[T]`, `Scope(...)` |
| Периодический/join/absent/debounced | `Trigger`, `JoinConsume`, `AbsentConsume`, `debounce=True` |

### Потоки

| Что нужно | Что использовать |
| --- | --- |
| Канонический чат-UI | `ChatEvent` из `Chat.stream()` / `create_chat_router` |
| Живой UI / Studio / token deltas | `RunEvent` из `Runtime.astream_events(stream_mode=...)` |
| Простой CLI/статус | `ProgressEvent` из `Runtime.astream()` (legacy status stream) |

### Persistence и память

| Что нужно | Что использовать |
| --- | --- |
| Продолжить разговор | `SessionStore` + `session_id` |
| Ограничить/суммаризовать prompt | `ChatMemory` (`WindowPruner` / `RollingDigestSummarizer`) |
| Помнить факты между сессиями | `MemoryStore` (`recipes`) |
| Посмотреть/отыграть ход | `ContextCheckpoint` (`context.checkpoints()` / `at()`) |
| Попробовать альтернативные пути | `context.branch()` + `merge()` |

### Владение ресурсами

`RuntimeResources.aclose()` закрывает `llm` и `embedder`, а также каждый
typed-ресурс, зарегистрированный с `owned=True` — в обратном порядке
регистрации, до провайдеров. Закрытие duck-typed: async `aclose()` или sync
`close()`.

```python
resources.register(
    ImageRenderSettings,
    ImageRenderSettings(provider=image_from_env(), out_dir=...),
    owned=True,
)
...
await resources.aclose()  # owned-ресурсы, затем embedder, затем llm
```

Всё остальное (`sources`, общие клиенты, `MemoryStore`, пулы БД) принадлежит
приложению: оставляйте `owned=False` (по умолчанию) и закрывайте в lifespan, а
не на каждый ход. Для callable `resources=` это важно: `ChatAssistant`
закрывает per-turn инстанс после каждого хода, поэтому помечайте только то, что
создала сама фабрика.
