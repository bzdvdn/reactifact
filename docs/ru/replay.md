# Replay (§55)

Replay отвечает на конституционный вопрос — **«почему агент произвёл этот
ответ?»** — конкретно. Коммиты детерминированы (§14), поэтому состояние контекста
восстанавливается *без запуска агентов*, а каждый LLM-вызов можно записать —
и запуск *воспроизводится точно*.

## Запись и воспроизведение на уровне провайдера

`ReplayLLM` — студия записи вызовов модели. Два прохода:

```python
from reactifact.replay import ReplayLLM

# проход 1 — записать настоящий запуск
resources = RuntimeResources(
    llm=ReplayLLM("calls.jsonl", mode="record", inner=real_llm)
)
runtime.run()                          # дописывает каждый вызов в calls.jsonl

# проход 2 — воспроизвести без сети
resources = RuntimeResources(llm=ReplayLLM("calls.jsonl", mode="replay"))
runtime.run()                          # те же артефакты, те же ответы
```

- **`mode="record"`** оборачивает реальный провайдер и дописывает каждую пару
  `(request → response)` (model, temperature, response_format, messages →
  text, usage) одной JSONL-строкой.
- **`mode="replay"`** отвечает *точно* на записанные вызовы. Вызов, не
  совпадающий с записью, бросает `ReplayMiss` — нельзя отвечать неверным
  результатом (§59). На уровне `structured_llm` miss честно деградирует в `None`
  (обычный путь фолбэка).

Так как детерминированные пути (гарды, вычисления, маршрутизация) не меняются,
повторённый запуск даёт идентичные артефакты — и по воспроизведённому состоянию
можно пройтись (или отрисовать `context_to_mermaid`), чтобы объяснить ответ.

## Проверить, что запуск воспроизводится

Запись фиксирует модель, но запуск воспроизводим только если детерминировано и
всё остальное — обычно ломают авто-id артефактов (`uuid4`), wall-clock время и
случайность. `verify_run` прогоняет пайплайн несколько раз под записанной
моделью **и строгими детерминированными id**, затем сравнивает `context_hash`:

```python
from reactifact import Context, Runtime
from reactifact.replay import verify_run


async def build(resources):
    context = Context(resources=resources)
    context.create(Question(...))
    await Runtime(context, agents=AGENTS).arun()
    return context


report = await verify_run(build, recording="calls.jsonl")   # прогонит дважды
assert report.ok, report.hashes
```

Расхождение — это реальная недетерминированность в вашем коде (нестабильный id,
`time.time()`, `uuid4()` в данных артефакта, порядок), а не разброс модели:
`report.hashes` показывает разошедшиеся отпечатки. Когда ресурсы строите сами,
передача `RuntimeResources(id_factory=counter_ids())` даёт артефактам без
явного id стабильные вида `Model:0000` вместо `uuid4` — часто это и есть всё
исправление. Так делает пример [`fintech_audit`](../../examples/fintech_audit),
поэтому повторный прогон печатает тот же `context sha256`.

## Детерминированный реплей состояния

Чекпоинт сессии несёт полную цепочку коммитов. Восстанавливайте состояние на
конкретном коммите без исполнения агентов:

```python
from reactifact.replay import replay_context, replay_summary
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.session import SessionStore

store = SessionStore(SQLiteKVBackend("sessions.sqlite3"))
context = await replay_context(store, session_id, version=7)   # состояние на коммите 7
print(replay_summary(context))                                  # счётчики, по типам
```

## CLI

```bash
python -m reactifact replay sessions.sqlite3 --session demo --diagram
```

Печатает сводку воспроизведённого состояния (`version · artifacts · relations ·
pending questions`, разбивка по типам артефактов) и, с `--diagram`, граф
провенанса в Mermaid. `--version` отматывает на конкретный коммит.