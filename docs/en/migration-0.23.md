# Migrating to reactifact 0.23.0

This guide is for applications that already use reactifact. It covers the
new facade and authoring surface added in 0.23.0; the runtime, artifacts and
provenance model are unchanged.

The short version:

| 0.22 style | 0.23 style |
| --- | --- |
| `ChatAssistant(..., user_message=Q, agents=[...])` | `Chat(..., seed_type=Q, groups=[...])` |
| `create_message` + a category `ContextVar` | `seed_factory(text, session_id, request)` |
| `stream()` override around the turn | `per_turn(text, session_id, request)` context manager |
| `Runtime(ctx, agents=[...])` | `Runtime(ctx, groups=[...])` or `steps=[...]` |
| tool collaborator via closures/globals | `Annotated[Client, Inject()]` (+ `Depends` alias) |
| tool returns a string to parse | tool returns a pydantic model / `list[Model]` |
| `Tracer(store=...)` manually | `Pipeline(traces=...)`, `Chat(traces=...)` |
| recipe subclasses | `Recipe.from_funcs(...).pipeline(...)` |

`ChatAssistant` is still available as the low-level engine behind `Chat`; it is
no longer the recommended entry point.

## 1. Chat: `ChatAssistant` → `Chat`

Before:

```python
assistant = ChatAssistant(
    store=store,
    groups=[echo_group],
    user_message=UserMsg,
    reply=reply,
    session_state=session_state,
    resources=lambda: build_resources(),
)
```

After:

```python
chat = Chat(
    store=store,
    groups=[echo_group],
    seed_type=UserMsg,
    answer_type=Reply,
    reply=reply,
    session_state=session_state,
    resources=lambda: build_resources(),
)
```

- `seed_type=` replaces `user_message=`;
- `answer_type=` lets `chat.answer()` return the typed artifact;
- `invoke/stream/history` keep working;
- `chat.router()` is the FastAPI router, `chat.test_client()` the isolated
  in-memory client;
- `chat.traces=`/`trace_sample=`/`lifespan=` mirror `Pipeline`.

### Category, tenant or user per turn

Before, apps often used a `create_message` hook and a `ContextVar`. After,
take the value from `request` in `seed_factory`:

```python
def seed(text: str, session_id: str, request: Mapping[str, object]) -> UserQuery:
    return UserQuery(
        text=text,
        session_id=session_id,
        category=str(request.get("category") or ""),
    )

chat = Chat(..., seed_type=UserQuery, seed_factory=seed)
await chat.invoke("...", session_id="s1", request={"category": "helm"})
```

`create_message` is still accepted for HITL/resume flows; `seed_factory` is the
short path for normal turns. For turn-scoped contextvars (a vault, a knowledge
namespace), use `per_turn`, an async context manager:

```python
@asynccontextmanager
async def per_turn(text, session_id, request):
    token = set_current_vault(SecretVault(...))
    try:
        yield
    finally:
        reset_current_vault(token)

chat = Chat(..., per_turn=per_turn)
```

`quick.chat_agent(...)` now returns `Chat`; it still accepts `groups=`/`steps=`
and the deprecated `agents=` alias.

## 2. Groups, steps and aliases

`Runtime(ctx, agents=[...])` is a deprecated alias. Use the keyword that
describes what you pass:

```python
Runtime(ctx, groups=[MyGroup()])     # StepGroup instances
Runtime(ctx, steps=[MyStep()])       # raw steps, wrapped in implicit groups
```

`Agent`/`create_agent` remain deprecated aliases for `StepGroup`/
`create_step_group`; new code should use the canonical names. `agents=` cannot
be combined with `steps=`/`groups=`.

## 3. Tools: injection, typed results, policies

A plain-class parameter is injected from `RuntimeResources` and omitted from
the JSON schema:

```python
@tool
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    return Pod(name=await k8s.get(name))
```

When the type would be ambiguous (a dataclass, a protocol, a scalar-looking
service), mark it explicitly:

```python
from typing import Annotated
from reactifact import Inject

@tool
async def get_pod(name: str, k8s: Annotated[K8sClient, Inject()]) -> Pod:
    ...
```

`Depends` is a FastAPI-style alias for `Inject`. Both forms are supported:

- `Annotated[T, Inject()]` — preferred;
- `param: T = Inject()` — shorthand.

Other 0.23 tool changes:

- a pydantic return fills `ToolOutput.data`; a non-empty `list[Model]` becomes
  `{"items": [...]}` instead of `str(list)`;
- `@tool(retry=RetryPolicy(...), timeout=...)` retries exceptions and
  `ToolError(retryable=True)`, and bounds one attempt;
- `ToolUse` reports `ToolError.code`/`retryable` to the model;
- outside a step, pass resources explicitly:
  `execute_tool(tool, args, resources=... )`.

## 4. Recipes: from subclasses to functions

`PlanExecute` and `ReflectionLoop` can still be subclassed, but the function
form is shorter and builds the group/pipeline for you:

```python
flow = PlanExecute.from_funcs(
    goal_type=Goal,
    step_type=PlanStep,
    result_type=StepResult,
    final_type=FinalAnswer,
    plan=plan,
    execute_step=execute_step,
    finish=finish,
)
pipeline = flow.pipeline(seed_type=Goal)
```

`ReflectionLoop.from_funcs(...)` follows the same shape. `flow.group()` returns a
`StepGroup` for an existing `Runtime`.

## 5. Tracing: sampling and retention

The manual `Tracer(store=...)` wiring is no longer required:

```python
pipeline = Pipeline(steps=[...], traces=True, trace_sample=SamplingPolicy(rate=0.1))
chat = Chat(steps=[...], seed_type=Q, traces="traces.db")
```

Old traces can be pruned from the CLI:

```bash
reactifact traces prune traces.db --keep 5000 --days 30 --dry-run
reactifact traces prune traces.db --keep 5000 --days 30 --keep-failed --keep-tag incident
```

See [observability](observability.md) for `RetentionPolicy` and sampling.

## 6. Pipeline parity

- `Pipeline.answer(seed)` / `await Pipeline.aanswer(seed)` return the typed
  `answer_type`, like `Chat.answer()`.
- `Pipeline(trace_sample=...)` is honoured by `TestPipeline` as well;
  `TestPipeline.answer()`/`aanswer()` delegate.
- `TestChat.override(Type, instance)` replaces a typed resource in the test
  client.

## 7. Compatibility and removals

- `Produce` / `ProduceCall` / `@produce` are removed; use `Step`/`@step`/
  `StepCall`.
- `ChatAssistant`, `Agent`, `create_agent`, `agents=`, `Agent.produces` and
  `Artifact.at/history/diff` remain deprecated bridges through 0.24.0. New
  code should use `Chat`, `StepGroup`, `create_step_group`, `groups=`, `steps`
  and `Context.version_history`/`artifact_version`/`version_diff`.
- The tool-module shims (`reactifact.tool_use`, `reactifact.native_tool_use`,
  `reactifact.agent_tool`) remain importable through this compatibility window;
  use `reactifact.tools.use`/`.native`/`.agent`.

## 8. Migration checklist

1. Replace `ChatAssistant(..., user_message=..., agents=...)` with
   `Chat(..., seed_type=..., groups=...)`.
2. Move per-turn metadata into `seed_factory`/`request`; move contextvars into
   `per_turn`.
3. Replace `Runtime(..., agents=[...])` with `groups=`/`steps=`.
4. Mark ambiguous tool collaborators with `Inject()` and return pydantic
   models/lists.
5. Replace recipe subclasses with `from_funcs(...)` where the function form
   fits.
6. Switch trace wiring to `traces=`/`trace_sample=` and add a retention policy.
7. Run the test suite and search for the old spellings:

   ```bash
   rg "ChatAssistant|agents=|user_message=|reactifact\.tool_use|@produce"
   ```
