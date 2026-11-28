# Переход на reactifact 0.30.0

0.30 закрывает compatibility-поверхность 0.x. Переехали две вещи:

- **авторство** — `Step` объявляет, что он *читает* и *производит*; `Agent`
  владеет контрактом пробуждения (`consumes`) и политикой исполнения;
- **исполнение** — `Pipeline` стал определением, `PipelineRunner` запускает его
  с `RunConfig`, а `Chat` принимает тот же `RunConfig` вместо россыпи
  runtime-аргументов.

Всё ниже — механическое переименование или переписывание одной строки;
deprecation-шимов, которые бы это сглаживали, больше нет.

## `Step` читает, `Agent` просыпается

Шаг больше не решает, когда ему запускаться. Он объявляет типизированные
входы; агент объявляет, что его будит.

Было:

```python
@step(consumes=[Consume(Question)], produces=Route, reads=Question)
async def classify(call: StepCall) -> None:
    ...
```

Стало — агент из одной функции:

```python
@agent(consumes=[Consume(Question)])
async def classify(question: Question) -> Route:
    return Route(kind="billing" if "invoice" in question.text else "general")
```

Стало — общий шаг внутри многошагового агента:

```python
@step(reads=Question, produces=Route)
async def classify(question: Question) -> Route:
    ...


intake = Agent("intake", steps=[classify, reply], consumes=[Consume(Question)])
```

`Agent(steps=...)` в 0.30 не выводит набор пробуждений: передавайте `consumes=`
явно. С `REACTIFACT_DEV=1` (или `REACTIFACT_ENV=dev`) конструктор предупреждает,
если аргумент забыт, — в разработке это видно сразу, а не как молча простаивающий
агент.

### Связанные удаления

| Удалено | Замена |
| --- | --- |
| `@step(consumes=...)`, `Step.consumes` | `Agent(consumes=...)` / `@agent(consumes=...)` |
| `Step.reacts_to` | `Read.on_create(...)`, `Read.on_update(...)`, `Read.on_stale(...)` |
| `Step.also_creates` | `produces=(Primary, Extra)` |
| `OnCreate` / `OnUpdate` / `OnDelete` / `OnStale` | `Consume.on_create` / `on_update` / `on_stale` / `on_delete`, либо `Read` с теми же classmethod |
| `StepGroup`, `create_step_group`, `StepGroupSpan` | `Agent` |
| `groups=` в `Runtime`/`Pipeline`/`Chat`/`subflow`/`swarm` | `agents=` |
| `Pipeline(routes=[...])` | `Pipeline(agents=[route])` |
| `Runtime(agents=[*route.agents(), ...])` | `Runtime(agents=[route, ...])` |
| `ContractReport.groups`, `GroupContract` | `ContractReport.agents`, `AgentContract` |
| `[tool.reactifact] groups` в `pyproject.toml` | `[tool.reactifact] agents` |
| `StepCall` | `StepInvocation` (те же поля и каналы) |
| `Agent.produces` | `Agent(steps=...)` / `steps = [...]` |
| `reactifact.commit.Read` | `CommitRead` |
| `ProduceRun` | `StepRun` |
| `RunTrace.group` | `RunTrace.agent` |
| `consume=` у `TurnContextStep` / `ScopedCleanup` | `read=` |
| `MemoryStore.remember_after_commit(effects, ...)` | вернуть `remember_later(...)` из шага |
| `RuntimeResources(trace_truncate=...)` | `Tracer(limits=TraceLimits(max_data_chars=...))` |
| `"detail"` в теле HTTP-ошибки | `error.message` |
| `MemoryStore.remember_from` | `remember_artifact` |
| `ReflectionLoop`/`PlanExecute` `.group()` / `.consumes()` | `.agent()` / `.reads()` |
| модуль `reactifact.step` | `reactifact.agent.step` (или верхнеуровневые `Step`/`step`) |

`Route.groups()` теперь `Route.agents()`, а routed-кейсы принимают объекты
`Agent` напрямую.

## `Pipeline` — определение, `PipelineRunner` — исполнение

`Pipeline` больше не несёт ресурсы, tracing, budget, lifespan и методы запуска.
Это живёт в `RunConfig`, а запускать умеет только runner.

Было:

```python
pipeline = Pipeline(
    agents=[classify],
    seed_type=Question,
    answer_type=Route,
    llm=llm,
    traces=True,
)
result = pipeline.run(Question(text="..."))
app = pipeline.http()
```

Стало:

```python
pipeline = Pipeline(agents=[classify], seed_type=Question, answer_type=Route)
runner = PipelineRunner(pipeline, config=RunConfig(llm=llm, traces=True))

result = runner.run(Question(text="..."))
app = runner.http()
```

Поля `RunConfig`: `resources`, `llm`, `budget`, `tracer`, `max_concurrency`,
`isolate_errors`, `on_agent_error`, `lifespan`, `traces`, `trace_sample`.

| Удалено у `Pipeline` | Замена в `PipelineRunner` |
| --- | --- |
| `resources=`, `llm=`, `budget=`, `tracer=`, `max_concurrency=`, `isolate_errors=`, `on_agent_error=`, `lifespan=`, `traces=`, `trace_sample=` | те же имена внутри `RunConfig(...)` |
| `run` / `arun` | `runner.run` / `runner.arun` |
| `answer` / `aanswer` / `astream` | `runner.answer` / `runner.aanswer` / `runner.astream` |
| `http` / `as_tool` / `test_client` / `close` | `runner.http` / `runner.as_tool` / `runner.test_client` / `runner.close` |
| `override` / `clear_overrides` | `runner.override` / `runner.clear_overrides` |
| `async with pipeline` (lifespan) | `async with runner` |
| `pipeline.last_result` / `last_context` | `runner.last_result` / `runner.last_context` |

`Pipeline.validate()`, `.describe()` и `.mermaid()` статические и остаются на
определении. `Daemon` следует тому же разделению: `Daemon(pipeline,
config=RunConfig(...))` собирает runner сам, `Daemon(runner)` переиспользует
готовый.

`create_pipeline_router` / `create_http_app` принимают `PipelineRunner`, а не
`Pipeline`.

## `Chat` берёт runtime-настройки из `RunConfig`

Было:

```python
chat = Chat(
    agents=[classify],
    seed_type=Question,
    answer_type=Route,
    llm=llm,
    traces=True,
    budget=budget,
    max_concurrency=4,
    lifespan=lifespan,
)
```

Стало:

```python
chat = Chat(
    agents=[classify],
    seed_type=Question,
    answer_type=Route,
    config=RunConfig(llm=llm, traces=True, budget=budget, max_concurrency=4, lifespan=lifespan),
)
```

Per-turn hook `resources=` остаётся прямым аргументом конструктора: это хук, а
не настройка запуска. `Chat(pipeline=...)` по-прежнему выводит seed/answer типы
и агентов; в `config=` переехали только runtime-ручки.

## Что остаётся

`StepInvocation` — по-прежнему низкоуровневый escape hatch для императивных шагов и
ручной работы с эффектами: например, текущий гайд по портированию в
[migrating.md](migrating.md) использует именно его. Для нового кода
предпочитайте типизированные параметры и аннотацию возврата; `StepInvocation` нужен,
когда шагу требуются trigger-артефакт, `event`, `progress()` или батч `effects`
напрямую.

`Runtime(agents=...)`, `Runtime(steps=...)`, `ChatAssistant`, subflow и рецепты
не изменились, кроме написания `agents=`.
