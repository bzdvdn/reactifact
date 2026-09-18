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

`Artifact` — пара `(id, data, created_at, …)`, где `data` — модель `pydantic`.
Артефакты — **полноценные объекты**, а не строковые блобы.

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

Авторская поверхность — **`self.effects`** (см. [Контракт produce](effects.md),
§24): produce пишет `create/update/link/ask` и возвращает `None`. Рантайm
компилирует набор эффектов в один атомарный **`Patch`** — скомпилированный
транспорт.

```python
async def produce(self, call: ProduceCall) -> None:
    answer = self.effects.create(Answer(query_id=qid, text=text), id="answer:q1")
    answer.link("supported_by", evidence_id)
    self.effects.update(some_artifact, status="answered")
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

`Agent` — **тонкий контейнер**: он объявляет, на что реагирует и что может
производить. Логика живёт в классах `Produce`.

```python
class RepairFlow(Agent):
    name = "repair_flow"
    consumes = [Consume(UserMsg), Consume(Project)]
    produces = [
        CollectStage(), PickStage(), PlanStage(),
        EstimateStage(), ApprovalStage(), AssistantStage(),
        Produce(ChatReply), Produce(PendingQuestion),
    ]
```

- `consumes` — типы артефактов, которые будят агента (и питают его входы —
  `Consume(..., wakes=False)` читает тип как вход, не будя на нём агента;
  `Consume(..., debounce=True)` схлопывает несколько событий одного
  поколения в один запуск). `reactifact.consume.JoinConsume`/`AbsentConsume`
  коррелируют по общему ключу сразу два типа артефактов, а не только один —
  см. [patterns](patterns.md).
- `produces` — экземпляры `Produce`, которые могут выполниться, когда агент
  проснулся. `Produce(reacts_to=(Type, …))` ограничивает produce только теми
  триггерящими событиями, которые ему реально важны, когда несколько produce
  одного агента хотят не одного и того же события; produce, объявивший ещё
  и необязательный параметр `trigger`, получает резолвленный артефакт
  напрямую вместо сырого `event` — гарантированно не `None` для
  CREATED/UPDATED/STALE события — см. [patterns](patterns.md).
- Runtime будит агентов по событиям, соблюдая бюджет и параллельность.

У `Agent` есть и низкоуровневый `run(event, context) -> Patch | None`,
который можно переопределить напрямую вместо декларирования `produces`. Это
эскейп-хетч для ручной сборки `Patch` (см. [patterns.md](patterns.md), когда
это действительно оправдано) — не третий повседневный стиль наравне с
подклассами `Produce` и функциями `@produce`. Прибегайте к нему, только
когда переросли оба.

## 5. Produce

`Produce[M]` — место, где происходит работа. Его **авторская поверхность —
`self.effects`** (§24): produce пишет, что должно измениться
(`effects.create/update/link/ask`), и возвращает `None`. Рантайm компилирует
слот эффектов в один атомарный патч — коммит, события, трейс, валидация те же,
но сам produce никогда не собирает `Patch` (этот тип теперь транспорт рантайма).

```python
class EstimateStage(Produce[Project]):
    artifact_type = Project

    async def produce(self, call: ProduceCall) -> None:
        ...
        self.effects.update(project_art, stage="estimate")
        return None
```

`produce()` всегда принимает ровно один аргумент, `call` — `ProduceCall` с
`.context`/`.inputs`/`.event`/`.trigger`/`.effects`. Про `.trigger`
(резолвленный артефакт за `.event`, гарантированно не `None` вместе с
`reacts_to`) — см. [patterns](patterns.md).

`fan_out_sources` / `materialize_doc` (рецепты) тоже пишут в текущий слот
эффектов, а HITL — это `effects.ask(...)` (артефакт `PendingQuestion`, §60).

Соглашение: **сначала детерминизм**. Право на запуск определяется гардом —
`if project.stage != "estimate": return None`. Должна ли стадия измениться —
чистая функция состояния. LLM применяется только к действительно генеративным
задачам и всегда обёрнут структурной схемой и фолбэком.

`Produce` может также объявлять зависимости (запускаться после других
производителей) через механизм `depends_on`/`inject` — см. [справочник API](api.md).

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

- **`Budget` / `RunOutcome` / `RunStats`** — лимиты на запуски, итерации и время;
  при исчерпании бюджета runtime останавливается и сообщает причину.
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
  запросами, поверх KV-чекпойнта (файл или SQLite).