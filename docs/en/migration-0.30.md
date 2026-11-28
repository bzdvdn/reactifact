# Migrating to reactifact 0.30.0

0.30 closes the 0.x compatibility surface. Two things moved:

- **authoring** — a `Step` declares what it *reads* and *produces*; an `Agent`
  owns the *wake* contract (`consumes`) and the execution policy;
- **execution** — `Pipeline` is a definition, `PipelineRunner` runs it with a
  `RunConfig`, and `Chat` takes the same `RunConfig` instead of loose runtime
  arguments.

Everything below is a mechanical rename or a one-line rewrite; there are no
deprecation shims left to bridge the gap.

## `Step` owns reads, `Agent` owns consumes

A step no longer decides when it runs. It declares typed inputs; the agent
declares what wakes it.

Before:

```python
@step(consumes=[Consume(Question)], produces=Route, reads=Question)
async def classify(call: StepCall) -> None:
    ...
```

After — one-function agent:

```python
@agent(consumes=[Consume(Question)])
async def classify(question: Question) -> Route:
    return Route(kind="billing" if "invoice" in question.text else "general")
```

After — shared step inside a multi-step agent:

```python
@step(reads=Question, produces=Route)
async def classify(question: Question) -> Route:
    ...


intake = Agent("intake", steps=[classify, reply], consumes=[Consume(Question)])
```

`Agent(steps=...)` derives no wake set in 0.30: pass `consumes=` explicitly.
With `REACTIFACT_DEV=1` (or `REACTIFACT_ENV=dev`) the constructor warns when it
is missing, so development catches the omission instead of a silently idle
agent.

### Related removals

| Removed | Use |
| --- | --- |
| `@step(consumes=...)`, `Step.consumes` | `Agent(consumes=...)` / `@agent(consumes=...)` |
| `Step.reacts_to` | `Read.on_create(...)`, `Read.on_update(...)`, `Read.on_stale(...)` |
| `Step.also_creates` | `produces=(Primary, Extra)` |
| `OnCreate` / `OnUpdate` / `OnDelete` / `OnStale` | `Consume.on_create` / `on_update` / `on_stale` / `on_delete`, or `Read` with the same classmethods |
| `StepGroup`, `create_step_group`, `StepGroupSpan` | `Agent` |
| `groups=` on `Runtime`/`Pipeline`/`Chat`/`subflow`/`swarm` | `agents=` |
| `ContractReport.groups`, `GroupContract` | `ContractReport.agents`, `AgentContract` |
| `[tool.reactifact] groups` in `pyproject.toml` | `[tool.reactifact] agents` |
| `StepCall` | `StepInvocation` (same fields and channels) |
| `Agent.produces` | `Agent(steps=...)` / `steps = [...]` |
| `reactifact.commit.Read` | `CommitRead` |
| `ProduceRun` | `StepRun` |
| `RunTrace.group` | `RunTrace.agent` |
| `consume=` on `TurnContextStep` / `ScopedCleanup` | `read=` |
| `MemoryStore.remember_after_commit(effects, ...)` | return `remember_later(...)` from the step |
| `RuntimeResources(trace_truncate=...)` | `Tracer(limits=TraceLimits(max_data_chars=...))` |
| `"detail"` in the HTTP error body | `error.message` |
| `reactifact.step` module | `reactifact.agent.step` (or the top-level `Step`/`step`) |

`Route.groups()` is now `Route.agents()`, and routed cases accept `Agent`
objects directly.

## `Pipeline` is a definition; `PipelineRunner` executes

A `Pipeline` no longer carries resources, tracing, budget, lifespan or
execution methods. Those live in `RunConfig`, and the runner is the only way to
run.

Before:

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

After:

```python
pipeline = Pipeline(agents=[classify], seed_type=Question, answer_type=Route)
runner = PipelineRunner(pipeline, config=RunConfig(llm=llm, traces=True))

result = runner.run(Question(text="..."))
app = runner.http()
```

`RunConfig` fields: `resources`, `llm`, `budget`, `tracer`, `max_concurrency`,
`isolate_errors`, `on_agent_error`, `lifespan`, `traces`, `trace_sample`.

| Removed on `Pipeline` | Use on `PipelineRunner` |
| --- | --- |
| `resources=`, `llm=`, `budget=`, `tracer=`, `max_concurrency=`, `isolate_errors=`, `on_agent_error=`, `lifespan=`, `traces=`, `trace_sample=` | the same names inside `RunConfig(...)` |
| `run` / `arun` | `runner.run` / `runner.arun` |
| `answer` / `aanswer` / `astream` | `runner.answer` / `runner.aanswer` / `runner.astream` |
| `http` / `as_tool` / `test_client` / `close` | `runner.http` / `runner.as_tool` / `runner.test_client` / `runner.close` |
| `override` / `clear_overrides` | `runner.override` / `runner.clear_overrides` |
| `async with pipeline` (lifespan) | `async with runner` |
| `pipeline.last_result` / `last_context` | `runner.last_result` / `runner.last_context` |

`Pipeline.validate()`, `.describe()` and `.mermaid()` are static and stay on the
definition. `Daemon` follows the same split: `Daemon(pipeline,
config=RunConfig(...))` builds its own runner, `Daemon(runner)` reuses one.

`create_pipeline_router` / `create_http_app` take a `PipelineRunner`, not a
`Pipeline`.

## `Chat` takes runtime settings through `RunConfig`

Before:

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

After:

```python
chat = Chat(
    agents=[classify],
    seed_type=Question,
    answer_type=Route,
    config=RunConfig(llm=llm, traces=True, budget=budget, max_concurrency=4, lifespan=lifespan),
)
```

The per-turn `resources=` callable stays a direct constructor argument, because
it is a hook rather than a run setting. `Chat(pipeline=...)` still infers the
seed/answer types and agents; only the runtime knobs moved into `config=`.

## What stays

`StepInvocation` is still the low-level escape hatch for imperative steps and raw
effect handling — for example the current porting guide in
[migrating.md](migrating.md) uses it. Prefer typed parameters and the return
annotation for new code; reach for `StepInvocation` when a step needs the trigger
artifact, `event`, `progress()`, or the `effects` batch directly.

`Runtime(agents=...)`, `Runtime(steps=...)`, `ChatAssistant`, subflows and the
recipes are unchanged apart from the `agents=` spelling.
