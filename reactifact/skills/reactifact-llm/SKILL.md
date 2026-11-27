---
name: reactifact-llm
description: Add and shape LLM steps in reactifact — configure a provider, get structured output into a typed artifact, use the LLMAgent/HITL tool loops, write prompts, and bound token/cost with a Budget. Use whenever a reactifact pipeline needs a model call, structured generation, tool calling, a system prompt, retries on bad JSON, or a per-turn token/cost limit, or when an LLM step is flaky, hallucinating, or over budget. Not for retrieval (`reactifact-rag`) or step/consume wiring (`reactifact-agents`).
---

# LLM steps in reactifact

The LLM is a **resource**, not a framework. `RuntimeResources(llm=...)` provides
a provider-agnostic `LLMProvider`; deterministic code owns control flow, and the
model only does reasoning, its output validated into a typed artifact. Because
the provider is injectable, the whole pipeline runs with `FakeLLM` in tests.

```python
# not-run: illustrative
ctx = Context(resources=RuntimeResources(llm=from_env()))
```

`reactifact.providers.from_env()` picks a provider from environment keys
(`OPENROUTER_API_KEY` first, else a local one). Explicit factories exist too
(`openai_llm`, `anthropic_llm`, `gemini_llm`, `openrouter_llm`, …). Details:
`references/providers.md`.

## Structured output → a typed artifact

`structured_llm` makes one call, parses JSON tolerantly, retries on failure, and
returns the schema instance or `None` (never a half-parsed dict):

```python
import asyncio
from pydantic import BaseModel
from reactifact import FakeLLM


class Sentiment(BaseModel):
    label: str


llm = FakeLLM('{"label": "positive"}')
result = asyncio.run(llm.structured(Sentiment, user="I love it"))
assert result is not None and result.label == "positive"
```

- `validate=Callable[[T], bool]` adds a **domain-rule** check (e.g. "the total
  must equal the sum of lines", "the cited id exists"); a rejection retries like
  a parse error, and `repair(invalid_or_None, last_reply) -> str` customises the
  retry instruction.
- `on_error(reason, exc)` reports *why* a `None` came back
  (`"no_provider"`, `"provider_error"`, `"parse_error"`, `"validation_error"`)
  without changing the `None` contract.
- `llm.structured_json(my_dict_schema, user=...)` is the same for a raw JSON
  Schema; `llm.reply(system=..., user=...)` returns plain text or `None`;
  `StructuredLLM(schema).call(llm, user)` is the reusable instance form.
  `structured_llm(llm, ...)`, `json_schema_llm(llm, ...)` and
  `llm_reply(llm, ...)` are the function equivalents — they no longer take a
  `Context`.

## Structured agent = generate → artifact

`StructuredGenerateAgent` is the one-liner agent for "LLM → schema → artifact".
Declare `schema`, override `build_prompt(inputs)`; the generated model is
created as an artifact (so it is versioned, traced and consumable downstream):

```python
# not-run: illustrative — define Question/Sentiment for real
from reactifact import Consume
from reactifact.llm_agent import StructuredGenerateAgent


class Classify(StructuredGenerateAgent):
    name = "classify"
    schema = Sentiment
    consumes = [Consume(Question)]

    def build_prompt(self, inputs):
        question = inputs[0].data
        return f"Classify the sentiment of: {question.text}"


agent = Classify()
```

`schema` is required: constructing the agent without one raises `ValueError`.
Each run stores the result under a stable id derived from the trigger, so a
re-run refreshes the artifact instead of creating a duplicate.

Override `fallback(inputs) -> schema | None` for a deterministic path when the
provider is offline or the model keeps failing — this is how an LLM step stays
testable and never blocks a run.

## Tool calling

`LLMAgent` runs a tool loop: it reads the provider, lets the model choose tools,
executes them, and finally produces a `ToolAnswer`. Add `steps=[...]` (merged
with the internal tool step in 0.25) to consume that answer and create domain
artifacts. Tools come from the `@tool` decorator over an `async def` (the JSON
schema is derived from the signature):

```python
# not-run: illustrative
from reactifact import Consume
from reactifact.llm_agent import LLMAgent
from reactifact.tools import tool


@tool
async def search(query: str) -> str:
    """Search the knowledge base."""
    return lookup(query)


agent = LLMAgent(
    name="researcher",
    system="Use search before answering.",
    tools=[search],
    consumes=[Consume(Question)],
)
```

Configure the agent by keyword — `system`, `tools`, `consumes`, `steps` for
the final answer, `max_steps`, and the sampling options. Subclassing is only for
when class attributes read better; it is never required.

Mark a tool `destructive=True` to route it through the HITL approval gate
(`HITLLMAgent`/`ToolUseHITL`); blocking `LLMAgent`/`ToolUse` does not offer
destructive tools to the model at all. `HITLLMAgent` also lets the model ask
clarifying questions (`PendingQuestion`) instead of guessing. Details and the
full tool contract: `references/tools.md`.

### Native vs JSON tool loops

`ToolUse` has three modes: `"json"` (the original structured decision loop),
`"native"` (provider-native `tool_calls` through `run_tool_loop`, including
DSML/textual parsing), and `"auto"` (native first, JSON fallback when the
provider returns no usable answer). `NativeToolUse` is the `"native"` shorthand.
Native mode also honours `context.resources.redactor`.

Give tools a localized progress template; the loop formats it with the args and
passes it through the redactor:

```python
# not-run: illustrative
@tool(progress="Смотрю настройки {service}")
async def kb_values(service: str) -> str:
    """Read deployment values."""
    ...
```

### Customising `ToolUse`

Subclass `ToolUse`/`ToolUseHITL` when the loop needs a domain-specific
observation or output. Use `self.progress(...)` for UI events (no extra
parameter) and return `Emit`/`Ensure` when the output needs a stable id; the
base `run()` accepts any return-style value:

```python
# not-run: illustrative — Answer/Observation are your models
from reactifact import Artifact, Context, Effects, Emit
from reactifact.tools.use import Observation, ToolAnswer, ToolUse


class ResearchToolUse(ToolUse):
    produces = (ToolAnswer, Observation)

    async def run(
        self,
        context: Context,
        effects: Effects,
        trigger: Artifact[object] | None,
    ) -> Emit | None:
        if trigger is None:
            return None
        self.progress("Planning the next tool call…")
        text = await self._loop(context, getattr(trigger.data, "text", ""))
        return Emit(
            Observation(query_id=trigger.id, text=text),
            id=f"observation:{trigger.id}",
        )
```

`context` is still there for tools that need it, but ordinary steps should use
`self.progress` / typed inputs instead of carrying it everywhere.

Tools are typed and dependency-injected, like steps:

```python
# not-run: illustrative — Pod, K8sClient are your app types
from pydantic import BaseModel
from reactifact import RetryPolicy, RuntimeResources, tool


class Pod(BaseModel):
    name: str


@tool(retry=RetryPolicy(max_attempts=3, backoff=0.5), timeout=10)
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    """Read one pod; `k8s` comes from RuntimeResources."""
    return Pod(name=await k8s.get(name))


resources = RuntimeResources(llm=from_env())
resources.register(K8sClient, K8sClient())
```

- a parameter annotated with a plain class (`K8sClient`) is **injected**, not
  part of the model's JSON schema; register it with
  `resources.register(Type, instance)` or `Pipeline.override(...)`;
- use `Annotated[T, Inject()]` when the type would be ambiguous (a dataclass,
  a protocol, a scalar-looking service); `Inject` is exported from
  `reactifact` (`Depends` is a FastAPI-style alias);
- a pydantic return value lands in `ToolOutput.data` (the model still sees JSON);
- `retry=` retries exceptions and `ToolError(retryable=True)`, `timeout=`
  bounds one attempt;
- outside a step, call
  `execute_tool(get_pod, {"name": "web"}, resources=resources)`.

## Prompts

`PromptTemplate("{question.text} — {k}")` renders declared `{var}` placeholders
strictly (`KeyError` on a missing variable) and exposes a stable `.hash`
(template + defaults) for drift detection; `.partial(**defaults)` makes a
variant and `required_variables` lists what is still needed.
`MessagesPrompt([(role, template), ...])` renders a chat sequence to
`list[Message]`, accepts reusable `PromptTemplate` rows and shared `defaults=`.
For a system/user call, `PromptPair(SYSTEM, USER).render(...)` returns a
`RenderedPrompt`; `**rendered.as_kwargs()` passes `system`, `user` and the
`prompt_hash` together. `PromptSequence([base, domain, safety])` joins several
system sections into the single system message these helpers send. To put a
placeholder inside a literal JSON object, double the object's outer braces:
`'{{"answer": "{answer}"}}'`.

## Bounding cost

Wrap a turn in a `Budget`:

```python
# not-run: illustrative
from reactifact import Budget, Runtime

runtime = Runtime(ctx, agents=agents, budget=Budget(max_tokens=20_000, max_cost=0.50))
```

Budgets bound **runs, iterations, seconds, tool calls, tokens and cost**; a
token/cost budget needs a `RuntimeResources.pricer` (there is no built-in price
table). The runtime stops the *next* step once the limit is crossed, so a turn
can overshoot by at most one call. `runtime.last_stats` reports what was used and
why it stopped (`RunOutcome`). `reactifact.cache.CachingLLM` dedupes identical
calls (exact match, no semantic cache) and `reactifact.routing.RouterLLM` adds
provider failover; both are ordinary `LLMProvider` wrappers.

## Failure modes to design for

- **Never trust free text.** Parse into a schema; `None` is a first-class
  outcome the pipeline handles (retry, fallback, or refuse) — not an exception.
- **Keep logic out of the prompt.** Ordering, counting, gating and math belong
  in Python; the model only judges/phrases.
- **Always provide a deterministic fallback** (or a no-LLM path) so tests and
  offline runs work.

## Where to look

- `references/providers.md` — provider factories, caching, failover, replay.
- `references/tools.md` — `@tool`, `Tool`, `ToolOutput`, `LLMAgent` internals.
- `docs/en/providers.md`, `docs/en/recipes.md`, `docs/en/api.md`.
