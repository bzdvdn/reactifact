# Контракт produce и ментальная модель (§24)

Один абзац, который стоит держать в голове, читая любой пример или пиша
produce:

> **Produce описывает, что должно измениться, записывая effects; рантайм
> компилирует эти эффекты в один атомарный патч и коммитит его. Вам почти
> никогда не нужно собирать `Patch` самому — это транспорт рантайма.**

```python
from reactifact import Effects

async def run(self, doc: Document, turn: Turn, effects: Effects) -> None:
    if <guard>:                     # право на запуск — решение состояния
        return None
    # эффекты: «дифф, выраженный как намерение»
    evidence = effects.create(Evidence(...), id="evidence:q1")
    answer = effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)     # doc: Artifact
    answer.link("supported_by", evidence)    # evidence: хэндл эффекта
    effects.update(turn, status="answered")
    effects.ask("Утвердить смету?", kind="approval")   # HITL (§60)
    return None
```

Класс-шаг, который пишет эффекты, объявляет параметр `effects: Effects` в
`run()` — рантайм инжектит слот, скоуженный на прогон. Функция `@produce`/
`@step` получает единственный аргумент `call: ProduceCall` и пишет
`call.effects`; это один и тот же набор эффектов, просто разный способ до
него добраться (у функциональной формы нет `self`).

## Идемпотентность: `create_once` и `upsert`

Пересобираемому id (`f"answer:{qid}"`) обычно нужен гард — почти в каждом
produce перед `effects.create(...)` он и стоит. `create_once` сворачивает
его прямо в вызов:

```python
handle = effects.create_once(Answer(...), id=f"answer:{qid}")
if handle is None:
    return None  # уже отвечено — делать нечего
```

`Create` на существующий id и так работает как create-or-refresh (новая
версия той же сущности, §42/§43) — `upsert` просто явно называет это
намерение там, где «может уже существовать» — это план, а не сюрприз:

```python
effects.upsert(Summary(...), id=f"summary:{doc_id}")
```

## Внешние эффекты: `act` (outbox)

`create`/`update`/`link`/`ask` меняют *состояние*. Письмо, webhook, деплой
меняют *мир* и не должны повториться при replay или retry. Запишите намерение —
runtime выполнит его после коммита:

```python
effects.act(
    "notify", key=f"notify:{order_id}:email", payload={"to": email}
)
```

`act` создаёт `PendingAction` под стабильным id `action:{key}` (поэтому retry
или ветка, пришедшая к тому же действию, не создают второе). Produce
**не** выполняет I/O сам: передайте `Runtime(dispatcher=...)`, и runtime
вызовет его один раз на закоммиченное намерение, записав `dispatched`/`failed`.
Полный диспетчер — в [паттернах — Outbox](patterns.md#outbox-side-effects),
контракт at-least-once — в [durability](durability.md).

## Три слоя

| Слой | Что это | Кто пишет |
| --- | --- | --- |
| **Produce** | реакция: guard → LLM/расчёт → `effects.*` → `None` | приложение (вы) |
| **Effects** | заявленный набор изменений (creates/updates/links/questions) на ход | вы, через инжектированный `effects` |
| **Patch** | *скомпилированные* операции, которые рантайм применяет одним коммитом | рантайм (и легаси/продвинутые сборки) |

Ничего не применяется, пока produce не завершится — **атомарность структурная**
(§41), никакого rollback. События, валидация по `produces`, трейс
reads/writes/relations — всё строится из тех же скомпилированных операций.

## Почему effects инжектятся

Слот эффектов скоужен на прогон: рантайм ставит его перед каждым исполнением
и снимает после (contextvar) — безопасно при параллельных produce. Класс-шаг
называет его параметром (`effects: Effects`); функциональный шаг добирается до
того же слота через `call.effects`. Вы его не создаёте и не передаёте явно.
Хэндлы собираются поперёк стейтментов (`evidence` создан выше, связан ниже) —
поэтому вызовы читаются «про артефакты», а не «про id».

## Где `Patch` ещё появляется

- **`Agent.run`** — люк для кастомных (не-Produce) агентов, собирающих набор
  изменений вручную; рантайм сшивает их после эффектов.
- **Рецепты** (`fan_out_sources`, `materialize_doc`) и `StatusMachine` пишут в
  слот; tool-цикл (`ToolUse`/`ToolUseHITL`) и HITL (`effects.ask`) — тоже
  эффекты.
- **Тесты и продвинутые сборки** могут строить `Patch`; в обычных produce он
  не нужен.

## Правило

```text
guard → решить → описать (effects) → return None
```

Если внутри produce хочется написать `Patch()` — остановитесь и пишите
эффекты; компилирует рантайм.

## Функция-форма `@produce` / `@step` — тот же авторский слой

Декоратор-produce получает тот же `call.effects` — один аргумент, `call`,
ровно как в класс-форме выше:

```python
from reactifact import produce

@produce(Answer)
async def answer_turn(call):
    if not call.inputs:
        return None
    qid = call.inputs[0].id
    ans = call.effects.create(Answer(text=...), id=f"answer:{qid}")
    call.effects.link(ans, "derived_from", call.inputs[0])
    call.effects.update(turn, status="answered")
    return None
```

Return-контракт сохраняется: возврат модели / списка моделей / `Patch` /
`None` компилируется рантаймом, так что короткие produce остаются
однострочными — `def f(call): return Answer(...)` читает
`call.context`/`call.inputs`/`call.trigger`, что нужно, и возвращает
результат вместо явного `call.effects.create(...)`.

## Какой стиль выбрать

Подкласс и функция `@produce` выше — два канонических стиля: подкласс, когда
у produce своя логика, достойная класса, `@produce` — для короткого
разового produce. Ещё одна вещь, которую принимают `Produce`/`Agent`, в этот
список *намеренно* не входит:

- Прямой override `Agent.run(self, event, context) -> Patch`, минуя
  `effects`/`Produce` целиком, чтобы собрать `Patch` вручную. Низкоуровневый,
  внутренний люк для случаев, которые effects принципиально не могут
  выразить — ни один пример в репозитории его не использует, только тесты
  самого фреймворка.
