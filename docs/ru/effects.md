# Контракт produce и ментальная модель (§24)

Один абзац, который стоит держать в голове, читая любой пример или пиша
produce:

> **Produce описывает, что должно измениться, записывая `self.effects`; рантайm
> компилирует эти эффекты в один атомарный патч и коммитит его. Вам почти
> никогда не нужно собирать `Patch` самому — это транспорт рантайма.**

```python
async def produce(self, call: ProduceCall) -> None:
    if <guard>:                     # право на запуск — решение состояния
        return None
    # эффекты: «дифф, выраженный как намерение»
    evidence = self.effects.create(Evidence(...), id="evidence:q1")
    answer = self.effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)     # doc: Artifact
    answer.link("supported_by", evidence)    # evidence: хэндл эффекта
    self.effects.update(turn, status="answered")
    self.effects.ask("Утвердить смету?", kind="approval")   # HITL (§60)
    return None
```

`call` (`ProduceCall`) — единственный аргумент, который получает любой
produce: `.context`/`.inputs`/`.event`/`.trigger`/`.effects` (последнее — то
же самое, что `self.effects` выше; пригодится для функциональной формы ниже,
у которой нет `self`).

## Идемпотентность: `create_once` и `upsert`

Пересобираемому id (`f"answer:{qid}"`) обычно нужен гард — почти в каждом
produce перед `self.effects.create(...)` он и стоит. `create_once` сворачивает
его прямо в вызов:

```python
handle = self.effects.create_once(Answer(...), id=f"answer:{qid}")
if handle is None:
    return None  # уже отвечено — делать нечего
```

`Create` на существующий id и так работает как create-or-refresh (новая
версия той же сущности, §42/§43) — `upsert` просто явно называет это
намерение там, где «может уже существовать» — это план, а не сюрприз:

```python
self.effects.upsert(Summary(...), id=f"summary:{doc_id}")
```

## Три слоя

| Слой | Что это | Кто пишет |
| --- | --- | --- |
| **Produce** | реакция: guard → LLM/расчёт → `self.effects.*` → `None` | приложение (вы) |
| **Effects** | заявленный набор изменений (creates/updates/links/questions) на ход | вы, через `self.effects` |
| **Patch** | *скомпилированные* операции, которые рантайm применяет одним коммитом | рантайm (и легаси/продвинутые сборки) |

Ничего не применяется, пока produce не завершится — **атомарность структурная**
(§41), никакого rollback. События, валидация по `produces`, трейс
reads/writes/relations — всё строится из тех же скомпилированных операций.

## Почему `self.effects` «амбиентен»

`self.effects` живёт в слоте, скоуженном на прогон: рантайm ставит его перед
каждым исполнением и снимает после (contextvar) — безопасно при параллельных
produce и **невидимо** для вас: вы его не создаёте, не называете, не передаёте.
Хэндлы собираются поперёк стейтментов (`evidence` создан выше, связан ниже) —
поэтому вызовы читаются «про артефакты», а не «про id».

## Где `Patch` ещё появляется

- **`Agent.run`** — люк для кастомных (не-Produce) агентов, собирающих набор
  изменений вручную; рантайm сшивает их после эффектов.
- **Рецепты** (`fan_out_sources`, `materialize_doc`) и `StatusMachine` пишут в
  слот; tool-цикл (`ToolUse`/`ToolUseHITL`) и HITL (`effects.ask`) — тоже
  эффекты.
- **Тесты и продвинутые сборки** могут строить `Patch`; в обычных produce он
  не нужен.

## Правило

```text
guard → решить → описать (self.effects) → return None
```

Если внутри produce хочется написать `Patch()` — остановитесь и используйте
`self.effects`; компилирует рантайm.

## Функция-форма `@produce` — тот же авторский слой

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