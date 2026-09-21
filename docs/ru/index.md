# reactifact

**Аудируемые ответы, а не ещё один граф — агенты преобразуют типизированные,
версионируемые артефакты с провенансом внутри эволюционирующего контекста, с
хешем, который можно перезапустить и проверить.**

Большинство агентных фреймворков просят **нарисовать граф** и довериться
арифметике модели. В процессе, который должен выдержать проверку — закрытие
квартала, аудит, комплаенс-ревью — ответ, который нельзя воспроизвести, это
ответ, который нельзя приложить. `reactifact` делает **аудируемым сам ответ**:
типизированный, версионируемый, связанный с источниками, воспроизводимый по
`context_hash`. Модель рассуждает; детерминированные части остаются в коде;
каждое утверждение несёт провенанс. Агенты реагируют на изменения состояния, а
runtime сам выводит, что может запуститься дальше — графа рисовать не нужно.

## Суть: проверяемый ответ

[`examples/fintech_audit`](../../examples/fintech_audit) — CSV транзакций, CSV
бюджета и документ политики, **без API-ключа**. Число никогда не считает модель;
это делает обычный Python, а ответ связан со своими доказательствами:

```text
variance vs budget: +12.5%   ($45,000 факт vs $40,000 бюджет, порог 10% → превышен)
answer sha256 5461290d…  ·  context sha256 24449f6f…

повторный прогон даёт тот же хеш — или проверьте сохранённый запуск:
  reactifact replay <store> --session <id> --verify 24449f6f…
```

![fintech_audit demo: вариация, ответ и воспроизводимый context hash — повторный прогон печатает тот же хеш.](../img/fintech-audit-short.gif)

## Как это работает

```text
                                   EVENT (created/updated)
                                          │
                                          ▼
CONTEXT ───────► ARTIFACTS ──► AGENTS REACT ──self.effects──► EFFECTS
   ▲                 │                                            │
   │                 │                                            │  compile
   └────── CONTEXT' ◄────────────────┘ PATCH ◄────────────────────┘
```

Вы описываете _какие данные существуют_, _какие артефакты есть_ и _что агенты
могут с ними делать_. Остальное делает runtime.

## Ментальная модель

| Традиционный агент               | reactifact                                                           |
| -------------------------------- | ----------------------------------------------------------------- |
| Программа идёт по графу/плану    | Агенты **реагируют на изменения состояния**                       |
| Сообщения — строки               | **Типизированные артефакты** (`Claim`, `Evidence`, `Answer`, …)   |
| Оркестрация явная                | Оркестрация **выводится из состояния**                            |
| Компонент _возвращает_ результат | Produce **пишет effects** (`self.effects`) — компилирует runtime  |
| Ретраи/откаты вручную            | Контекст **версионируется** (коммиты как в git, diff, rollback)   |
| «Кто это произвёл?» теряется     | **Провенанс** связывает каждый производный артефакт с его входами |

## Почему effects вместо «вернуть изменение»?

В центре цикла — то, как produce вносит изменение. Многие фреймворки просят производителя _вернуть_ результат, а какой-то оркестратор применяет его.
reactifact переворачивает авторство: produce **формулирует, что должно
измениться**, через `self.effects` (create / update / link / ask) и возвращает
`None`; runtime компилирует набор эффектов в один атомарный патч — либо
применяется весь шаг, либо ничего.

```python
async def produce(self, call: ProduceCall) -> None:
    evidence = self.effects.create(Evidence(...), id="evidence:q1")
    answer = self.effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)
    answer.link("supported_by", evidence)
    self.effects.update(turn, status="answered")
    return None
```

Поскольку handle — это объекты, а не id, одно выражение может ссылаться на
артефакт, созданный другим. А так как компиляцией владеет runtime, ручной
сборки `Patch` не бывает. Участие человека — просто ещё один эффект
(`effects.ask(...)`). Подробнее — в [Почему reactifact](why-reactifact.md) и в
[контракте produce](effects.md).

## Почему артефакты вместо сообщений?

Сообщения непрозрачны; артефакты инспектируемы. Объект `Evidence` знает свой
текст, источник и оценку. Благодаря провенансу runtime отвечает на вопрос
_«почему агент так сказал?»_, проходя по связям
`Answer —supported_by→ Claim —derived_from→ Evidence —extracted_from→ Doc`.

## Почему версионируемый контекст?

Каждый запуск — это коммит:

- **Diff** — что именно изменилось между двумя ходами.
- **Rollback** — отменить неудачный шаг и перезапустить с чистого состояния.
- **Детерминированные повторы** — та же история даёт тот же результат.
- **Инспектируемость** — полная, запрашиваемая история всего, что произошло.

## Требования

- Python 3.11–3.14 (`.venv` управляется через `uv`; CI гоняет весь набор тестов на всех четырёх).
- `pydantic` для моделей артефактов; FastAPI/uvicorn — только для web-примеров.

## Быстрый старт

Два агента, и между ними не объявлено ни одной связи в графе — второй
реагирует потому, что появился результат первого, а ответ несёт *доказательство*
того, откуда он взялся:

```python
from pydantic import BaseModel

from reactifact import Budget, Consume, Context, Runtime, RuntimeResources, create_agent, produce


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


DOCS = {
    "refund": "Возврат возможен в течение 14 дней с момента покупки.",
    "pricing": "Тариф Pro — $49/месяц при годовой оплате.",
}


@produce(Evidence)
async def find_evidence(call):
    question = next((a for a in call.inputs if isinstance(a.data, Question)), None)
    if question is None:
        return None
    hit = next((v for k, v in DOCS.items() if k in question.data.text.lower()), None)
    if hit is not None:
        call.effects.create(Evidence(text=hit))


@produce(Answer)
async def answer_from_evidence(call):
    evidence = next((a for a in call.inputs if isinstance(a.data, Evidence)), None)
    if evidence is None:
        return None
    call.effects.create(Answer(text=evidence.data.text)).link("supported_by", evidence)


search_agent = create_agent("search", consumes=[Consume(Question)], produces=[find_evidence])
answer_agent = create_agent("answer", consumes=[Consume(Evidence)], produces=[answer_from_evidence])

ctx = Context(resources=RuntimeResources())
runtime = Runtime(ctx, agents=[search_agent, answer_agent], budget=Budget(max_runs=10))

ctx.create(Question(text="какая у вас политика возврата?"))
runtime.run()  # оба агента реагируют сами — никто их не связывал вручную

answer = ctx.latest(Answer)
evidence = ctx.related(answer.id, "supported_by")[0]
print(answer.data.text)                     # "Возврат возможен в течение 14 дней с момента покупки."
print("supported_by:", evidence.data.text)  # провенанс, который можно проследить, а не строка в логе
```

Это весь цикл: **создан артефакт → агенты реагируют → применён патч → версия
контекста продвинулась**. Всё остальное в этой документации надстраивается над
этим циклом.

Хотите чего-то ближе к реальному приложению? [Quickstart](quickstart.md) —
три рабочих, проверенных сниппета: tool-calling агент, поиск по своим
документам, чат-бот с сохраняемыми сессиями — каждый со ссылкой на полный
пример, из которого он урезан.

## Куда дальше

**Понять идею**

- [Почему reactifact](why-reactifact.md) — _дизайн-аргумент_: почему effects, почему
  без графа, почему детерминизм, почему версионируемое состояние.
- [Сравнение](comparison.md) — reactifact vs LangGraph/CrewAI по пунктам, и когда
  reactifact *не* стоит использовать.
- [Concepts](concepts.md) — Context, Artifact, Patch, Agent, Produce.

**Строить на этом**

- [Quickstart](quickstart.md) — три рабочих сниппета: tool-calling агент,
  поиск по своим документам, чат-бот с сохраняемыми сессиями.
- [Sources](sources.md) — откуда агенты берут информацию.
- [Providers](providers.md) — как подключить LLM/эмбеддер/провайдер
  изображений/речи в `RuntimeResources`.
- [Recipes](recipes.md) — готовые search fan-out, материализация референсов,
  машины состояний жизненного цикла.
- [Patterns](patterns.md) — переиспользуемые паттерны (reflection,
  map-reduce, supervisor, …), каждый — на конкретном примере.

**Эксплуатировать**

- [Observability](observability.md) — трейс каждого запуска: агентские спаны,
  чтения/записи, LLM-вызовы; офлайн-дашборд или экспорт в Langfuse/Postgres.
- [Диагностика проблем](troubleshooting.md) — «агент не запустился» / «запустился
  дважды» / «run остановился раньше времени» — по симптомам, не по фичам.
- [Evaluation](eval.md) — многоуровневая оценка финального `Context`
  (качество evidence, provenance grounding, корректность вычислений, …).
- [Branching & merge](branching.md) — форк состояния, исследование
  альтернатив, трёхстороннее слияние с явными конфликтами.
- [Replay](replay.md) — детерминированная реконструкция «почему агент
  ответил именно так», без повторного запуска агентов.
- [Visualization & CLI](viz.md) — Mermaid-диаграммы графа артефактов и трейса
  запуска; CLI-утилиты `reactifact`.
- [API reference](api.md) — все верхнеуровневые символы, по строке на каждый.

**Посмотреть в деле**

- [Examples](examples.md) — четырнадцать работающих приложений, которые можно запустить.
- [Port matrix](port-matrix.md) — какой классический паттерн LangGraph/CrewAI/DSPy
  соответствует какому примеру.
- [Design notes](design-notes/adaptive.md) — более глубокое обоснование
  адаптивного [планировщика](design-notes/adaptive.md); модель компиляции
  effects → Patch описана в [English design note](../en/design-notes/patches.md)
  (пока не переведена — это архивный лог обсуждения, актуальное поведение уже
  в [effects.md](effects.md)).
