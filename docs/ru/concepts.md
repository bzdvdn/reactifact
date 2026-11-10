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

`Patch` (контейнер) собирает рантайm и escape-хук `Agent.run`; эффекты
сочетаются *внутри* одного produce, поэтому ничего не применяется до компиляции
рантаймом — атомарность структурная (§41).

HITL — это `effects.ask(...)` → `PendingQuestion`, ответ через
`effects.resume(...)` (см. [patterns](patterns.md)).

## 4. Agent

`Agent` — **тонкий контейнер**: он группирует `Step`-ы. Каждый шаг объявляет,
что потребляет и производит; runtime выводит триггеры агента из этих
контрактов.

```python
class RepairFlow(Agent):
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
- `Agent.consumes` остаётся для imperative escape hatch (агент переопределяет
  `run(event, context)`), но декларативные агенты держат контракты на шагах.
- `Consume(..., wakes=False)` читает вход, не будя на нём; `debounce=True`
  схлопывает несколько событий одного поколения в один запуск;
  `Consume.by_base(Base)` будит и на наследниках. `JoinConsume`/
  `AbsentConsume` коррелируют типы — см. [patterns](patterns.md).
- Runtime будит агентов по событиям, соблюдая бюджет и параллельность.

`Agent.run(event, context) -> Patch | None` остаётся низкоуровневым escape
hatch для ручной сборки `Patch`; это не третий повседневный стиль.

## 5. Step

`Step` — место, где происходит работа. Класс-шаг объявляет
`consumes`/`produces` и получает типизированные входы плюс `effects: Effects` в
`run()`:

```python
from reactifact import Artifact, Effects, Step

class EstimateStage(Step):
    consumes = (Project,)
    produces = (Project,)

    async def run(self, project_art: Artifact[Project], effects: Effects) -> None:
        ...
        effects.update(project_art, stage="estimate")
```

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