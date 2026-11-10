# Переезд с reactifact 0.1.x на 0.20.0

Эта страница — для приложений, которые уже используют reactifact. Если вы
переносите код с LangGraph, CrewAI, LlamaIndex или обычного Python, начните с
[переезда с других фреймворков](migrating.md).

0.20.0 — ломающий релиз, но для большинства приложений миграция механическая:
рантайм и commit DAG те же, а авторский слой становится явно типизированным.
Старые имена ещё импортируются в окне совместимости 0.20; ниже — замены и
что запланировать к 0.21.0.

## 1. Что изменилось

| 0.1.x | 0.20.0 |
| --- | --- |
| `Produce` / `@produce` / `ProduceCall` | `Step` / `@step` / `StepCall` |
| `Agent.produces=[...]` | `Agent.steps=[...]` |
| контракты размазаны между `Agent.consumes` и `Produce.reacts_to` | каждый шаг владеет `consumes` / `produces` |
| `call.trigger` + ручные `isinstance`-проверки | типизированные входы `T` / `Artifact[T]` или `Consume(T)` |
| ручной `context.get(stage_id(...))` | `scope=` / `Scope(...)` + типы или `Latest[T]` |
| `self.effects.*` | `effects: Effects` в `run()` класса или `call.effects` в функции |
| мутабельный `Artifact` с `artifact.update()` | frozen-handle; изменения через `Context` / `Effects` |
| `artifact.history` / `.at()` / `.diff()` | `Context.version_history` / `artifact_version` / diff версий |
| v1 session payload | v2 `blobs` + `data_ref`; v1 мигрирует при загрузке |
| `reactifact.tool_use`, `reactifact.native_tool_use`, `reactifact.agent_tool` | `reactifact.tools.use`, `reactifact.tools.native`, `reactifact.tools.agent` |

Всё, что помечено как bridge, доступно в 0.20.x. Удаление планируется в
0.21.0, поэтому миграцию стоит закончить до этого релиза.

## 2. Шаги и агенты

Классовая форма сохраняет вид — объявления переезжают на шаг, а тело
получает типизированные входы:

```python
from reactifact import Artifact, Effects, Produce, Step


class Estimate(Produce):
    artifact_type = Project
    reacts_to = Project

    async def produce(self, call: ProduceCall) -> None:
        self.effects.update(call.trigger, stage="estimate")
```

становится:

```python
from reactifact import Artifact, Effects, Step


class Estimate(Step):
    consumes = (Project,)
    produces = (Project,)

    async def run(self, project: Artifact[Project], effects: Effects) -> None:
        effects.update(project, stage="estimate")
```

Агент перечисляет шаги вместо produces:

```python
agent = Agent(produces=[Estimate()])
```

становится:

```python
agent = Agent(steps=[Estimate()])
```

`Agent.produces` остаётся свойством совместимости. Новый код использует
`Agent.steps`; для интроспекции читайте `agent.steps`.

Функциональная форма меняет `@produce` на `@step`, а `self.effects` — на
`call.effects`:

```python
from reactifact import Answer, Evidence, step


@step(produces=Answer, consumes=Evidence)
async def answer(call):
    return Answer(text=call.trigger.data.text)
```

Функции-шаги тоже могут принимать типизированные входы:

```python
@step(produces=Answer, consumes=Evidence)
async def answer(evidence: Evidence, effects: Effects) -> Answer:
    return Answer(text=evidence.text)
```

## 3. Типизированные входы и корреляция

В 0.1.x шаг начинался с проверок:

```python
if call.event is None or not isinstance(call.trigger.data, Question):
    return None
question = call.trigger.data
```

В 0.20.0 контракт входа — это аннотация типа:

```python
class Research(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question, effects: Effects) -> Answer:
        return Answer(text=question.text)
```

Используйте `Artifact[T]` вместо `T`, если нужны handle, id или провенанс.
`list[Artifact[T]]` — для всех подходящих версий типа.

Корреляция, которая раньше была ручным поиском по stable-id, теперь `Scope`:

```python
class BuildAnswer(Step):
    consumes = (
        Consume(ResearchTurn),
        Consume(Evidence, wakes=False),
    )
    produces = (Answer,)
    scope = "query_id"

    async def run(
        self,
        turn: ResearchTurn,
        evidences: list[Artifact[Evidence]],
        effects: Effects,
    ) -> None:
        ...
```

`scope=` держит артефакты с одинаковым `query_id` вместе. `Consume(...,
wakes=False)` заменяет чтение входа без перезапуска на каждое обновление.
`Latest[T]` выбирает самое новое значение, когда шагу не нужна вся история.
`When(...)` — условия по значению, `Wakes(False)` — не будящие входы.

## 4. Эффекты

`self.effects` удалён. Классовый шаг получает `effects: Effects`; функция
использует `call.effects`:

| 0.1.x | 0.20.0 |
| --- | --- |
| `self.effects.create(value)` | `effects.create(value)` |
| `self.effects.update(artifact, **fields)` | `effects.update(artifact, **fields)` |
| `self.effects.link(source, "relation", target)` | `effects.link(source, "relation", target)` |
| `self.effects.announce(...)` | `effects.announce(...)` |

Возврат `BaseModel` из `run()` — это create-sugar. Всё сверх создания
объявленного выхода — обновления, связи, дополнительные create, HITL — идёт
через `effects`. Объявления по-прежнему валидируют коммит: каждый созданный тип
должен быть в `produces`; первым идёт основной output, затем дополнительные
create: `produces=(Receipt, PendingAction)` (старое `also_creates=` — deprecated
алиас).

## 5. Артефакты и история версий

`Artifact` теперь frozen-handle в content-addressed хранилище версий. Не
мутируйте его и не вызывайте `artifact.update()`.

| 0.1.x | 0.20.0 |
| --- | --- |
| `artifact.update(data)` | `ctx.update(artifact.id, data)` (возвращает новый handle) |
| `artifact.history` | `ctx.version_history(artifact.id)` |
| `artifact.at(version)` | `ctx.artifact_version(artifact.id, version)` |
| `artifact.diff(other)` | `ctx.version_diff(...)` / `ctx.diff(...)` |

Старые `Artifact.at/history/diff` остаются deprecated-bridges в 0.20.x.
Компакция авторитетна: `ctx.compact(keep_versions=...)` выбирает, какие версии
остаются читаемыми, а удалённые артефакты сохраняют читаемую историю. Код,
полагавшийся на мутабельный `_history`, должен читать версии через `Context`.

## 6. Сессии и персистентность

Session payload — v2: индекс версий артефактов плюс `blobs` по ключу
`(data_type, content_hash)`, а commit-операции `Create`/`Update` используют
`data_ref`.

Для существующих хранилищ код менять не нужно: `Context.from_dict` загружает
v1 payload, а следующее сохранение пишет v2. Если вы разбираете payload
напрямую, читайте версии через публичные методы `Context`, а не через старый
layout `data`/`history`.

## 7. Импорты инструментов

Старые top-level модули — shims. Предпочитайте пути пакета:

```python
from reactifact.tools import tool
from reactifact.tools.use import ToolUse, ToolUseHITL
from reactifact.tools.native import parse_tool_calls
from reactifact.tools.agent import AgentAsTool
```

| 0.1.x | 0.20.0 |
| --- | --- |
| `reactifact.tool_use` | `reactifact.tools.use` |
| `reactifact.native_tool_use` | `reactifact.tools.native` |
| `reactifact.agent_tool` | `reactifact.tools.agent` |

Shims остаются в 0.20.x и запланированы к удалению в 0.21.0.

## 8. Удалённые примеры

В дереве 0.1.x было много маленьких примеров, дублировавших одну и ту же идею.
0.20.0 оставляет десять reference-приложений: `devops`, `knowledge`, `forklab`,
`repair`, `medic_lab`, `fintech_audit`, `starter_app`, `chat`, `outbox` и
`repo_agent`; `tour` изолирует примитивы. Если старый
пример был вашей отправной точкой, возьмите ближайший из оставшихся и страницу
[паттернов](patterns.md). Удалённые примеры не входят в публичный API.

## 9. Чеклист миграции

1. Замените `Produce` на `Step`, `ProduceCall` на `StepCall`, `@produce` на
   `@step`.
2. Замените `Agent(produces=[...])` на `Agent(steps=[...])`; перенесите
   контракты `Agent.consumes` на соответствующие шаги.
3. Замените `self.effects.*` на инжектированный `effects` или `call.effects`
   в функциях.
4. Замените проверки `call.trigger.data` на типизированные параметры,
   `Consume(...)`, `Latest[...]` или `When(...)`.
5. Замените ручную корреляцию по stable-id на `scope=` / `Scope(...)`.
6. Замените `artifact.update()` на `ctx.update()`; историю читайте через
   `Context.version_history` / `artifact_version`.
7. Обновите импорты инструментов на `reactifact.tools.*`.
8. Найдите остатки:

   ```bash
   rg "self\.effects|Agent\(produces=|\.update\(.*artifact|artifact\.history|reactifact\.tool_use|reactifact\.native_tool_use|reactifact\.agent_tool" .
   ```

9. Запустите тесты и уберите `DeprecationWarning` от `@produce` и
   history-bridges. Ваш код не должен порождать эти предупреждения.
10. Если вы храните сессии вне `SessionStore`, проверьте, что сохранённый v1
    payload загружается и перезаписывается как v2 при сохранении.

## 10. Совместимость и удаление

Остаётся в 0.20.x, удаляется в 0.21.0:

- `Produce`, `ProduceCall`, `@produce`
- `Agent.produces`
- `Artifact.at`, `Artifact.history`, `Artifact.diff`
- `reactifact.tool_use`, `reactifact.native_tool_use`, `reactifact.agent_tool`

`@produce` выдаёт `DeprecationWarning`; алиасы и bridges — нет. Считайте их
помощниками миграции, а не вторым API.

Если для чего-то нет прямого соответствия, проверьте
[changelog 0.20.0](https://github.com/bzdvdn/reactifact/blob/master/CHANGELOG.md) и страницу [паттернов](patterns.md),
прежде чем открывать issue.
