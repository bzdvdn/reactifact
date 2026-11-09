# Контракт шага и effects (§24)

Один абзац, который стоит держать в голове, читая пример или пиша шаг:

> **Шаг описывает, что должно измениться, через effects — или возвращает свой
> результат; рантайм компилирует набор изменений в один атомарный патч и
> коммитит его. `Patch` почти никогда не собирают вручную — это транспорт
> рантайма.**

```python
from reactifact import Artifact, Effects

async def run(
    self,
    doc: Artifact[Document],
    turn: Artifact[Turn],
    effects: Effects,
) -> None:
    evidence = effects.create(Evidence(...), id="evidence:q1")
    answer = effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)
    answer.link("supported_by", evidence)
    effects.update(turn, status="answered")
    effects.ask("Утвердить смету?", kind="approval")   # HITL (§60)
```

Класс-шаг, который пишет эффекты, объявляет `effects: Effects` в `run()` —
рантайм инжектит слот, привязанный к запуску. Функция `@step` получает один
аргумент `call: StepCall` и пишет через `call.effects`; это один и тот же
набор эффектов, просто разные поверхности (у функции нет `self`).

## Возврат или effects

Возврат модели (или списка моделей) — это create-сахар:

```python
async def run(self, question: Artifact[Question]) -> Answer:
    return Answer(text=question.data.text)
```

`effects` нужны, когда шагу требуется явный id, update/link/delete, HITL или
несколько create. `None` означает «изменений нет» (guard или неподходящая
ветка).

## Идемпотентность: `create_once` и `upsert`

Пересобираемому id (`f"answer:{qid}"`) нужен гард — `create_once` сворачивает
его в вызов:

```python
handle = effects.create_once(Answer(...), id=f"answer:{qid}")
if handle is None:
    return None  # уже отвечено — делать нечего
```

`Create` на существующий id и так работает как create-or-refresh (новая версия
той же логической сущности, §42/§43); `upsert` — явное имя для этого:

```python
effects.upsert(Summary(...), id=f"summary:{doc_id}")
```

## Внешние эффекты: `act` (outbox)

`create`/`update`/`link`/`ask` меняют *состояние*. Письмо, webhook или деплой
меняют *мир* и не должны повторяться при replay/retry. Запишите намерение —
runtime выполнит его после коммита:

```python
effects.act(
    "notify", key=f"notify:{order_id}:email", payload={"to": email}
)
```

`act` создаёт `PendingAction` под стабильным id `action:{key}`. Шаг **не**
выполняет I/O сам: передайте `Runtime(dispatcher=...)`, и runtime вызовет его
один раз на закоммиченное намерение. См. [Outbox](patterns.md#outbox-side-effects)
и [durability](durability.md).

## Три слоя

| Слой | Что это | Кто пишет |
| --- | --- | --- |
| **Step** | реакция: guard → LLM/расчёт → `effects.*` или возврат | приложение (вы) |
| **Effects** | заявленный набор изменений (creates/updates/links/questions) на запуск | вы, через инжектированный `effects` |
| **Patch** | *скомпилированные* операции, применяемые одним коммитом | рантайм |

Ничего не применяется, пока шаг не завершится — **атомарность структурная**
(§41), без rollback-машинерии. События, валидация по `produces` и трейс
чтений/записей выводятся из тех же скомпилированных операций.

## Зачем effects инжектятся

Слот эффектов привязан к запуску: рантайм кладёт новый перед вызовом и убирает
после (contextvar) — безопасно при параллельных шагах. Класс-шаг получает его
параметром (`effects: Effects`), функция — через `call.effects`. Хэндлы можно
строить между выражениями (`evidence` создан выше и связывается ниже), поэтому
код читается «про артефакты», а не «про id».

## Где ещё встречается `Patch`

- **`Agent.run`** — escape hatch для кастомных агентов, собирающих change-set
  вручную.
- **Рецепты** (`fan_out_sources`, `materialize_doc`) и `StatusMachine` пишут в
  слот; tool loop (`ToolUse`/`ToolUseHITL`) и HITL (`effects.ask`) — тоже
  эффекты.
- **Тесты и продвинутая сборка** могут строить `Patch`; обычным шагам он не
  нужен.

## Правило

```text
guard → decide → describe (effects) → return None
```

Если внутри шага появляется `Patch()` — остановитесь и пишите effects; компиляцию
делает рантайм.

## Функциональная форма (`@step`) — та же поверхность

Декорированная функция получает `call` и использует `call.effects`:

```python
from reactifact import StepCall, step

@step(produces=Answer, consumes=Question)
async def answer_turn(call: StepCall) -> None:
    question = call.trigger
    answer = call.effects.create(
        Answer(text=...), id=f"answer:{question.id}"
    )
    answer.link("derived_from", question)
```

Возврат модели / списка / `Patch` / `None` компилируется рантаймом, поэтому
короткие шаги остаются однострочниками.

## Какой стиль выбрать

Подкласс и функция `@step` — два канонических стиля: подкласс, когда у шага
есть логика, достойная имени, `@step` — для короткой разовой операции.
Переопределение `Agent.run(self, event, context) -> Patch` остаётся
низкоуровневым escape hatch, а не повседневным стилем.
