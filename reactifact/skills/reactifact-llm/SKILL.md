---
name: reactifact-llm
description: Add and shape LLM steps in reactifact — configure a provider, get structured output into a typed artifact, use the LLMAgent/HITL tool loops, write prompts, and bound token/cost with a Budget. Use whenever a reactifact pipeline needs a model call, structured generation, tool calling, a system prompt, retries on bad JSON, or a per-turn token/cost limit, or when an LLM step is flaky, hallucinating, or over budget.
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
from reactifact import Context, FakeLLM, RuntimeResources
from reactifact.structured import structured_llm


class Sentiment(BaseModel):
    label: str


ctx = Context(resources=RuntimeResources(llm=FakeLLM('{"label": "positive"}')))
result = asyncio.run(
    structured_llm(ctx, schema=Sentiment, user="I love it")
)
assert result is not None and result.label == "positive"
```

- `validate=Callable[[T], bool]` adds a **domain-rule** check (e.g. "the total
  must equal the sum of lines", "the cited id exists"); a rejection retries like
  a parse error, and `repair(invalid_or_None, last_reply) -> str` customises the
  retry instruction.
- `on_error(reason, exc)` reports *why* a `None` came back
  (`"no_provider"`, `"provider_error"`, `"parse_error"`, `"validation_error"`)
  without changing the `None` contract.
- `json_schema_llm(ctx, my_dict_schema, user=...)` is the same for a raw JSON
  Schema; `llm_reply(ctx, system=..., user=...)` returns plain text or `None`;
  `StructuredLLM(schema).call(ctx, user)` is the reusable instance form.

## Structured agent = generate → artifact

`StructuredGenerateAgent` is the one-liner agent for "LLM → schema → artifact".
Declare `schema`, override `build_prompt(inputs)`; the generated model is
created as an artifact (so it is versioned, traced and consumable downstream):

```python
# not-run: illustrative — define Question/Sentiment for real
from reactifact import Consume, create_agent
from reactifact.llm_agent import StructuredGenerateAgent


class Classify(StructuredGenerateAgent):
    schema = Sentiment
    consumes = [Consume(Question)]
    produces = []  # StructuredGenerateAgent creates schema instances itself

    def build_prompt(self, inputs):
        question = inputs[0].data
        return f"Classify the sentiment of: {question.text}"


agent = create_agent("classify", consumes=[Consume(Question)], produces=[Classify()])
```

Override `fallback(inputs) -> schema | None` for a deterministic path when the
provider is offline or the model keeps failing — this is how an LLM step stays
testable and never blocks a run.

## Tool calling

`LLMAgent` runs a tool loop: it reads the provider, lets the model choose tools,
executes them, and finally produces the artifacts you declare. Tools come from
the `@tool` decorator over an `async def` (the JSON schema is derived from the
signature):

```python
# not-run: illustrative
from reactifact import Consume, create_agent
from reactifact.llm_agent import LLMAgent
from reactifact.tools import tool


@tool
async def search(query: str) -> str:
    """Search the knowledge base."""
    return lookup(query)


class Researcher(LLMAgent):
    system = "Use search before answering."
    tools = [search]
    consumes = [Consume(Question)]


agent = create_agent("researcher", consumes=[Consume(Question)], produces=[])
```

Mark a tool `destructive=True` to route it through the human-approval gate.
`HITLLMAgent` lets the model ask clarifying questions (`PendingQuestion`)
instead of guessing. Details and the full tool contract: `references/tools.md`.

## Prompts

`PromptTemplate("{question.text} — {k}")` renders declared `{var}` placeholders
strictly (`KeyError` on a missing variable) and exposes a stable `.hash` for
drift detection; `MessagesPrompt([(role, template), ...])` renders a chat
sequence to `list[Message]`. Pass `prompt_hash=template.hash` to a structured
call so the trace records which prompt version ran.

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
- `docs/en/llm.md`, `docs/en/structured.md`, `docs/en/providers.md`.
