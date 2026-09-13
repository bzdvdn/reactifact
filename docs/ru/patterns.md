# Паттерны

Переиспользуемые паттерны, встречающиеся в примерах. Они не
абстрактны — каждый конкретно воплощён в `examples/`.

## TL;DR

| Делай | Не делай | Почему |
| --- | --- | --- |
| Пиши `self.effects.create/update/link/ask(...)`, возвращай `None` | Собирай `Patch` вручную в обычном produce | Runtime компилирует один атомарный патч на produce — ручная сборка это лазейка, а не дефолт |
| Оформляй допустимость через ранний `return None` | Полагайся на порядок планирования, чтобы пропустить неготовую работу | Допустимость — решение по состоянию, а не удачное стечение планирования (§69) |
| Используй стабильные id (`answer:{qid}`) или `effects.create_once(...)` | Выводи id из счётчика или таймстампа | Идемпотентные перезапуски — одно и то же событие дважды не должно дублировать состояние |
| Возвращай `None` при отсутствии модели / неудачном парсинге, показывай честный fallback | Подставляй «уверенно звучащий» канонический ответ при сбое LLM-вызова | Детерминированная работа остаётся детерминированной; сбой генерации должен быть виден, а не замаскирован |
| Держи `next_status`/скоринг чистыми функциями `(context, key) -> ...` | Подмешивай LLM-вызовы или побочные эффекты в `StatusMachine.next_status` | Чистые функции тестируются без runtime |
| Используй `Runtime(isolate_errors=True, on_agent_error=...)` только когда осознанно решил, что частичный прогресс приемлем | Ставь `isolate_errors` по умолчанию, чтобы заглушить исключения | По умолчанию — fail-loud (§69); изоляция ошибок — явное продуктовое решение, а не страховка |

Каждая строка ссылается на полный паттерн ниже.

## HITL: человек как полноправный участник

Человек — просто ещё одна реакция на контекст. Runtime представляет вопрос
артефактом `PendingQuestion`:

```python
class PendingQuestion(BaseModel):
    question: str
    kind: str = "general"          # например "clarify", "approval"
    notes: dict[str, Any] = {}     # маршрутизация ("какой агент спросил")
```

Produce создаёт его через `self.effects.ask(...)`, а ответ человека приходит
через `self.effects.resume(...)` (эффект, помечающий `PendingQuestion` отвеченным,
§60):

```python
self.effects.ask("Утвердить смету?", kind="approval")
# ... web/UI видит ожидающий вопрос и показывает состояние «ожидание» ...
self.effects.resume(question_artifact, "да")
return None
```

Производящий агент видит ответ как новое событие (артефакт `PendingQuestion`
обновляется). Web-демо запрашивают `context.pending_questions()`, чтобы понять,
показывать ли состояние «ожидание».

Паттерн: **активировалась стадия → сразу спросить** (repair `ApprovalStage`
создаёт вопрос об одобрении в момент, когда становится активной, не дожидаясь
нового сообщения), затем **реагировать на ответ** следующим событием.

Для пересобираемого вопроса (`f"steer:{qid}:{round}"`, как в steering-вопросе
`medic-lab`) передайте `id=` в `effects.ask(...)`, чтобы гард
(`if context.get(id) is not None: return None`) не давал produce спрашивать
снова, пока вопрос ещё не отвечен — тот же идиом идемпотентности, что и у
`effects.create_once`.

## Инструментальные агенты: LLM + инструменты (блокирующий или HITL)

Для потоков «модель решает, какой инструмент вызвать» используйте встроенных
агентов:

```python
from reactifact import Consume, Produce
from reactifact.llm_agent import HITLLMAgent

class OpsAgent(HITLLMAgent):
    name = "ops"
    system = "You run Kubernetes/GitLab/Ansible tasks."
    tools = [...]        # экземпляры FunctionTool
    max_steps = 8
    max_asks = 2
    consumes = [Consume(Project)]
    produces = [Produce(Report)]
```

- `LLMAgent` — блокирующий цикл: LLM отдаёт `tool_call`, runtime выполняет
  инструмент, наблюдение идёт в следующий шаг. Человека в цикле нет.
- `HITLLMAgent` — то же плюс модель может выдать `ask`: создаётся
  `PendingQuestion`, цикл останавливается, ответ человека возвращается как
  `Observation(source="user")`. Само *исполнение* инструмента также
  контролируется `ToolUseHITL`, поэтому рискованные команды ждут нажатия человека.

Пример `devops` — каноническая демонстрация `HITLLMAgent` (LLM-роутер инструментов +
одобрение мутаций K8s/GitLab/Ansible).

## Структурный вывод: никогда не парсите сырой JSON сами

Runtime оборачивает один вызов LLM в схему `pydantic` с ретраями и терпимым
парсингом JSON:

```python
from reactifact.structured import StructuredLLM, structured_llm

# процедурный вариант:
body = await structured_llm(
    context, schema=AnswerBody,
    system="You assemble coherent answers.",
    user=f"Question: {question}\nFacts: {facts}",
)

# переиспользуемый объектный вариант:
_extractor = StructuredLLM(ProjectInfo, system="Extract repair facts; unknown = null")
facts = await _extractor.call(context, user=message_text)
```

Оба возвращают `None` при отсутствии модели или провале парсинга после ретраев —
и вызывающий обязан обработать `None` (см. фолбэки). Если нужно отличить
«провайдер не настроен» от «провайдер упал» (например, чтобы алертить на
реальный простой), не меняя саму обработку `None` — передайте `on_error`:

```python
def alert_if_down(reason: str, exc: Exception | None) -> None:
    if reason == "provider_error":
        logger.error("LLM outage: %r", exc)

body = await structured_llm(context, schema=AnswerBody, user=text, on_error=alert_if_down)
```

Строки `system`/`user` собирайте через `PromptTemplate` (ядро): объявленные
переменные, `KeyError` при нехватке, поля атрибутов модели
(`template.render(topic=…, question=…)`) — без ручного `.format` в коде приложения.

`StructuredGenerateAgent` — декларативная обёртка: переопределите
`build_prompt(inputs)`, опционально `fallback(inputs)`, объявите `schema` — чтение
и запись провенанса запишутся за вас.

## Plan-and-execute: план, затем шаг за шагом

Для цели, которая раскладывается на упорядоченную последовательность
*зависимых* шагов (в отличие от независимых чанков map-reduce выше), разделите
планирование и выполнение на два produce вместо одного большого tool-loop:

```python
class Planner(Produce[PlanStep]):
    artifact_type = PlanStep

    async def produce(self, context, inputs, event=None):
        goal = context.get(event.artifact_id) if event is not None else None
        if goal is None or context.list_artifacts(PlanStep):
            return None  # не Goal, либо план уже построен (§42)
        steps = await plan_steps(goal.data.text)  # structured LLM или фолбэк
        for index, instruction in enumerate(steps):
            self.effects.create(PlanStep(index=index, instruction=instruction), ...)


class Executor(Produce[StepResult]):
    artifact_type = StepResult

    async def produce(self, context, inputs, event=None):
        steps = sorted(context.list_artifacts(PlanStep), key=lambda s: s.data.index)
        for step in steps:
            if context.get(f"result:{step.id}") is not None:
                continue  # уже выполнен
            if step.data.index > 0 and context.get(f"result:{steps[step.data.index-1].id}") is None:
                return None  # ждём результат предыдущего шага (§69)
            self.effects.create(StepResult(...), id=f"result:{step.id}")
            return None  # один шаг за поколение; следующий результат перезапустит нас
```

Ключевой момент: исполнитель **не** ветвится по `event.artifact_id`, как это
делает поштучный produce в map-reduce — он на каждом триггере заново вычисляет
«какой шаг следующий» из состояния (подписан и на `PlanStep`, и на
`StepResult`) — тот же идиом «допустимость — решение по состоянию», что и у
`Combine` в `map_reduce`. Гард `if step.data.index > 0 and ... is None: return
None` — это и есть весь механизм упорядочивания: без явного графа
управления, без ручной проводки «жди узел N». Produce `Finisher` зеркалит
`Combine` из `map_reduce`: ждёт результата каждого шага, затем собирает
финальный ответ.

Полный порт — в `examples/plan_execute` (структурированное планирование с
детерминированным однотаговым фолбэком и финишером).

## Фолбэки: честная деградация

Детерминированная работа остаётся детерминированной; генеративная деградирует
*честно*:

1. Если **модель не настроена** — используйте детерминированный вариант
   (заготовленные варианты, фолбэк-планы): демо-режим без ключа.
2. Если **модель вернула ничего полезного** — НЕ подменяйте канонные ответы;
   сообщите о сбое открыто: *«Не удалось подобрать варианты…»*.

Пример `repair` реализует оба пути в `_make_design_options`: `fallback_options`
только когда `context.resources.llm is None`, иначе — явное сообщение о сбое.

## Модель изменений и отката («изменить → пересобрать»)

Длинные многоэтапные диалоги иногда должны *откатываться*. Пример `repair`
моделирует это так: разобрать запрос на изменение → определить самую раннюю
затронутую стадию → детерминированно сбросить всё ниже по потоку:

```python
target = rollback_target(changed)      # "plan" | "estimate" | …
updates = _downstream_resets(target)   # чистит design_options/plan/estimate
updates |= {"stage": target, "info": new_info, "handled_msg": ""}
```

Сброс `handled_msg` переустанавливает стадии, чтобы пересборка реально
запустилась. Это ручной близнец `StatusMachine` — для потоков, где откат — часть
продукта, а не жизненного цикла.

## Бюджет и справедливость

`Budget` ограничивает запуск:

```python
runtime = Runtime(ctx, agents=[...], budget=Budget(max_runs=200), max_concurrency=2)
```

- `max_runs`, `max_iterations`, `max_time_s`, лимиты вызовов инструментов — runtime
  останавливается и сообщает `RunOutcome` (`completed` | `budget_exhausted` | …)
  вместе с `RunStats`.
- `Agent.concurrency_limit` (у LLM-агентов по умолчанию ниже) + глобальный
  `max_concurrency` runtime держат лимиты провайдеров: демо `medic-lab` — лаборатория
  гипотез с лимитом LLM = 2 внутри глобального лимита 6.
- По умолчанию исключение одного агента пробрасывается из `arun()`/`astream()`
  и останавливает весь запуск (§69 — падать громко, не молчать). Изолировать
  его можно через `Runtime(isolate_errors=True, on_agent_error=...)`: такой
  агент не даёт патча в этом поколении, остальные агенты всё равно
  продвигаются, а сбой трейсится (`AgentSpan.error`) и считается
  (`RunStats.errors`), а не прячется.

## Память чата через сессии

Состояние живёт в контексте, поэтому *память чата — это просто состояние*. Между
запросами:

```python
store = SessionStore(FileKVBackend("sessions"))
session = await store.open(session_id, resources=resources)
# ...создать UserMsg, astream, await session.save()
```

`store.open` восстанавливает контекст из последнего чекпоинта; фоновый агент
(`@consume`/триггер) может ужимать историю, обновлять указатель `handled_msg` и
закрывать зависшие вопросы. Web-демо несут этот паттерн дословно.

## Машины состояний для длинных жизненных циклов

См. [recipes](recipes.md). Правило выбора паттерна:

| Ситуация | Подход |
| --- | --- |
| Артефакт проходит фазовые состояния | `StatusMachine` + верификатор-produce |
| Потоку нужно *откатываться* на правках пользователя | гард стадии + `_downstream_resets` |
| Исследовать и сравнивать альтернативные состояния | `branch()` + `merge()` (§39-§40) |
| «Который из этих выбрала модель?» | парсинг как в `PickStage` + гард |

## Детерминизм как привычка

- **Контракт produce**: пишите `self.effects.create/update/link/ask(...)` и
  возвращайте `None`; рантайm компилирует слот в один атомарный патч (§24).
  `Patch` — транспорт, в обычном produce его почти не печатают.
- Право на запуск — гард, а не удачный порядок планировщика (`return None` рано).
- Предпочитайте стабильные id (`answer:{qid}`, `ref:{sid}:{owner}`) →
  идемпотентные повторы. `self.effects.create_once(Model(...), id=...)`
  сворачивает гард «уже сделано» прямо в вызов — `None` в ответ значит
  пропустить, а не собирать гард руками перед каждым `create`.
- Чистые функции решений (`next_status`) тестируются без runtime.
- У каждого LLM-вызова — структурная схема, бюджет ретраев и путь `None`.