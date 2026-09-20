# Переезд с LangGraph / CrewAI / LlamaIndex / обычного Python

Переезжать целиком не нужно. Рекомендованный путь — **перенести один кусок,
держать его рядом со старым кодом и расти оттуда**: reactifact — это
библиотека, она композируется с тем, что уже работает. Эта страница — *как*;
[матрица портов](port-matrix.md) — *что во что отображается*, а каждый паттерн
ниже ссылается на запускаемый пример.

Если унести одну мысль:

> **Состояние первично, исполнение производно.** Вместо того чтобы рисовать, что
> выполнится дальше, вы описываете, какие артефакты существуют и что каждый
> агент потребляет/производит; рантайм решает, что запускать, по изменениям
> состояния.

## 1. Карта понятий

| У вас (LangGraph / LangChain / CrewAI / LlamaIndex) | reactifact |
| --- | --- |
| node + `add_edge` / `add_conditional_edges` | `Produce` + `Consume(Type)` — ребро *и есть* «кто потребляет этот тип» |
| общий `TypedDict`/dict state, мутируемый узлами | типизированные версионируемые `Artifact`'ы в `Context` |
| reducer (`Annotated[list, add]`) | `self.effects.create/update/link(...)`, компилируемые в один атомарный `Patch` |
| checkpointer / thread state | `Context` (git-подобный), `Session`/`SessionStore`, `context.branch()`/`merge()` |
| `interrupt()` / `Command(resume=...)` | `effects.ask(...)` → `PendingQuestion`, ответ через `effects.resume(...)` |
| subgraph / вложенный агент | `AgentAsTool` (изолированный вложенный рантайм) или `context.branch()` |
| conditional router / supervisor | `Consume.condition` / `Consume.by_field`, или `recipes.Router` |
| RAG-цепочка (retriever → prompt → LLM) | `Source` + `fan_out_sources` + `materialize_doc` + produces (`quick.rag` для простого случая) |
| tool loop (`create_react_agent`) | `LLMAgent`/`ToolUse` (или `ToolUseHITL` для approval'ов), либо `native_tool_use` / `recipes.run_tool_loop` |
| `MemorySaver` + summary-узел | артефакты `Msg` + `context.view` + `recipes.WindowSummarizer`/`RollingDigestSummarizer` |
| callbacks / LangSmith / Langfuse | нативные трейсы (`reactifact.tracing`) + `redactor=`, экспорт в Langfuse/OTLP/Postgres |

## 2. Переносим один узел (до → после)

Узел LangGraph, который классифицирует, а затем отвечает:

```python
def classify(state):            # узел
    return {"route": "billing" if "invoice" in state["text"] else "general"}

def answer(state):              # узел
    return {"reply": llm(state["text"], route=state["route"])}

graph.add_edge(START, "classify")
graph.add_edge("classify", "answer")
```

В reactifact *рёбра исчезают* — они становятся `consume`/`produce`:

```python
from pydantic import BaseModel
from reactifact import Consume, Produce, ProduceCall, produce


class Question(BaseModel):
    text: str


class Route(BaseModel):
    thread_id: str
    kind: str


class Reply(BaseModel):
    text: str


@produce(Route)
async def classify(call: ProduceCall) -> None:
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    kind = "billing" if "invoice" in question.data.text.lower() else "general"
    call.effects.create(Route(thread_id=question.id, kind=kind), id=f"route:{question.id}")


@produce(Reply)
async def answer(call: ProduceCall) -> None:
    route = call.trigger  # этот produce запускается, потому что создан Route
    if route is None or not isinstance(route.data, Route):
        return None
    # ... вызов модели с входом, ограниченным ходом ...
    call.effects.create(Reply(text="…"), id=f"reply:{route.data.thread_id}")
```

```python
from reactifact import Consume, Context, Runtime, create_agent

answerer = create_agent(
    "answerer",
    consumes=[Consume(Reply)],       # «ребро» от classify к answer
    produces=[answer],
)
```

Две вещи, на которые стоит обратить внимание — это и есть реальная работа при
переезде:

- **Право на запуск — это решение состояния, а не место в графе.** Старый граф
  говорил «answer идёт после classify». Здесь `answer` запускается, *потому что
  существует `Route`* — поэтому guard (`return None`, когда вход не готов) и есть
  механизм секвенирования, а не ребро.
- **Стабильные id.** `f"route:{question.id}"` делает повторные прогоны
  идемпотентными (§42) — одно и то же событие дважды не дублирует состояние. Это
  заменяет большую часть ручного учёта «уже сделано?».

## 3. State dict → артефакты

```python
# раньше: один растущий dict
state["facts"].append({"text": "...", "source": url})

# теперь: типизированные артефакты + провенанс
fact = self.effects.create(Fact(text="...", source=url))
fact.link("extracted_from", doc)
```

Ничего не мутируется на месте: update — это новая версия, поэтому
`context.diff(v1, v2)` — реальная операция, а провенанс
(`supported_by`/`derived_from`/…) — тот же граф, которым рантайм решает, что
перезапускать, а не логгер сбоку. См. [`examples/ledger`](../../examples/ledger/README.md)
— аргумент про пересчёт, сделанный наглядно.

## 4. Checkpointing, потоки, time-travel

- Диалоги сохраняйте через `SessionStore(FileKVBackend(...))` + `ChatAssistant`
  (`examples/support_copilot`, `examples/knowledge`).
- Форкайте разведку через `context.branch()`, гоняйте агентов на каждой ветке и
  `merge()` обратно — с настоящим `MergeConflict`, когда обе ветки трогают один
  артефакт (`examples/forklab`).
- Воспроизводите прошлое состояние через `reactifact.replay` / `replay_context`;
  фиксируйте отпечаток прогона через `reactifact.audit.context_hash`
  (`examples/fintech_audit`).

## 5. Interrupt → human-in-the-loop

```python
# спросить
self.effects.ask("Approve the refund?", kind="approve", id=f"approve:{qid}")
# ответить (из HTTP-хендлера / CLI)
context.resume(question.id, "yes")
```

Человек — просто ещё одна реакция (`PendingQuestion` — артефакт); тулы
гейтятся так же — см. `examples/repo_agent` (тул `@tool(destructive=True)`,
который выполняется только после одобрения) и `examples/devops`.

## 6. RAG-цепочка → источники

```python
from reactifact.quick import rag

r = rag({"docs": "./docs", "costs": "./costs.csv"})
answer = await r.ask("what's the total gpu cost?")   # answer.text, answer.sources
```

Для полного контроля соберите тот же пайплайн руками через `fan_out_sources` +
`materialize_doc` и произведите `Answer`, связанный `supported_by` с
документами (`examples/knowledge`, `examples/research`). Поиск — это
возможность `Source` (filesystem/CSV/embeddings/web), а не захардкоженная
цепочка: меняете источник, агенты остаются.

## 7. Tool-циклы

- Модель сама решает, какой тул вызвать, шаг за шагом: `LLMAgent` / `HITLLMAgent`
  (`examples/devops`).
- OpenAI-нативные `tools`/`tool_calls`: `reactifact.native_tool_use`
  (композируемые функции) или `recipes.run_tool_loop` для ограниченного,
  параллельного, mandatory-tool-aware цикла.
- Бюджеты и честные сбои идут бесплатно: `Budget(max_runs=…, max_tool_calls=…)`,
  а produce возвращает `None` вместо уверенной выдумки (§59).

## 8. «Всё или ничего» не обязательно (интероп)

- **Зовите reactifact из узла.** Ваш LangGraph-узел может собрать `Context`,
  прогнать `Runtime` и вернуть результат — переносите по одному
  аудируемому/вычисляющему шагу.
- **Отдайте reactifact наружу как MCP.** `create_mcp_server(tools, context=ctx)`
  публикует ваши `Tool`'ы (и read-only ресурсы `context://artifacts/...`), так
  что любой MCP-клиент — Claude, другой агентный фреймворк — может звать
  reactifact-часть.
- **Сосуществуйте по задаче.** Оставьте оркестрацию там, где она работает;
  применяйте reactifact там, где важнее всего провенанс, детерминизм и
  аудируемость (форма [fintech_audit](../../examples/fintech_audit/README.md)).

## 9. Ops-отображение

| Задача | reactifact |
| --- | --- |
| трейсинг | `Tracer(store=TraceStore(...))`, `LangfuseTracer`, `OTLPTracer`, `PostgresStore`; вычистка через `RuntimeResources(redactor=…)` |
| сессии | `SessionStore` поверх `FileKVBackend`/`SQLiteKVBackend`/`PostgreSQLKVBackend` |
| бюджеты/лимиты | `Budget(max_runs, max_seconds, max_iterations, max_tool_calls)` |
| политика ошибок | fail-loud по умолчанию; `Runtime(isolate_errors=True, on_agent_error=…)` — частичный прогресс |
| per-request данные | `Runtime.arun(request={...})` → `call.request` (без ручных ContextVar) |
| жизненный цикл ресурсов | `RuntimeResources.scope(factory)` / `async with` (loop-safe клиенты) |

## 10. Чеклист переезда

- [ ] Каждое промежуточное значение — **типизированный `Artifact`**, не dict/`TypedDict`.
- [ ] Каждая единица — `Produce`, объявляющий `consumes`/`produces`; ни один узел
      не зовёт другой напрямую.
- [ ] Право на запуск — guard (`return None`), а не порядок планирования.
- [ ] Id **стабильны и перевыводимы** (`f"answer:{qid}"`,
      `effects.create_once(...)`, `effects.create_once_from(...)`).
- [ ] Производные артефакты `link(...)` на входы (провенанс).
- [ ] Вызовы модели идут через `structured_llm`/`llm_reply` и возвращают `None`
      при сбое — вызывающий показывает честный фолбэк, не фейковый ответ.
- [ ] Для продакшн-прогонов задан `Budget`.
- [ ] Человеческие шаги — `effects.ask`/`resume`, не особый случай.
- [ ] Сессии — через `SessionStore`; ресурсы собираются на loop
      (`RuntimeResources.scope`).
- [ ] Трейсинг подключён (`Tracer(store=...)`), и `redactor=` задан, если данные
      чувствительны.

## 11. Грабли

- **`add_edge` нет.** Если хочется его добавить — ребро это `Consume(ProducedType)`
  на нижестоящем агенте.
- **`effects`, а не `return patch`.** Пишите `self.effects.*` и возвращайте
  `None`; рантайм компилирует набор эффектов в один атомарный коммит (возврат
  `Patch` из `Produce` — низкоуровневый escape hatch, не идиома).
- **Артефакты — не сообщения.** Вход модели собирается из `context.view(...)` /
  потреблённых артефактов, а не из свободного списка строк.
- **Детерминизм — это привычка.** Используйте стабильные id и держите
  таймстемпы/случайность вне того, что хешируете — тогда `context_hash` делает
  прогоны сравнимыми (`examples/fintech_audit`).

## 12. Куда смотреть дальше

- [Матрица портов](port-matrix.md) — канонический паттерн → пример reactifact.
- [Быстрый старт](quickstart.md) — четыре `quick`-кейса в несколько строк.
- [Семантика планировщика](scheduler-semantics.md) — контракт исполнения, на
  который вы переезжаете.
- [Паттерны](patterns.md) / [Рецепты](recipes.md) — переиспользуемые блоки.
