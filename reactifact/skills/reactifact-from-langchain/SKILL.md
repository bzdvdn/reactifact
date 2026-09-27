---
name: reactifact-from-langchain
description: Migrate LangChain or LangGraph agent code to reactifact, and translate between the two mental models. Use whenever the user has a LangChain/LangGraph chain, StateGraph, tool-calling agent or message-list memory and wants it in reactifact (or asks how the paradigms map), or is porting a RAG or multi-agent app off LangChain.
---

# Migrating from LangChain / LangGraph to reactifact

The core shift: LangGraph passes a mutable **state dict** between nodes you run
by hand; reactifact has **typed artifacts** and a runtime that schedules agents
when their inputs appear. There is no `state` to thread — you create artifacts
and the graph grows.

## Concept map

| LangChain / LangGraph | reactifact |
| --- | --- |
| `StateGraph`, `add_node`, `add_edge` | `create_agent(name, consumes=[...], produces=[...])` — edges are implicit in consume/produce types |
| mutable state dict + reducers | typed `BaseModel` artifacts in a `Context` |
| `RunnableLambda` / a chain step | a `Produce[T]` (an agent's transformation) |
| `@tool` / `Tool` | `reactifact.tools.tool` / `Tool` / `FunctionTool` |
| tool-calling loop (`AgentExecutor`) | `LLMAgent` (system + tools) or `tool_use` recipes |
| `MemorySaver` / checkpointer | `Session` + a checkpoint/KV store |
| `messages: list[...]` as memory | artifacts (`Question`/`Answer`/…) across turns, or `ChatMemory` |
| conditional edges | `Consume.by_status(...)` / `by_field(...)`, absent-consume gates |
| `app.invoke(...)` | `Runtime(ctx, agents=[...]).arun()` |
| LangSmith traces | `reactifact.tracing.TraceStore` (`tracing=[...]`) |
| `langchain.evaluation` / LangSmith evals | `reactifact.eval` (`docs/en/eval.md`) |

## Porting recipe

1. **Name the state.** Every key the graph's state dict carries becomes a
   `BaseModel` artifact type.
2. **Turn each node into a produce.** The node's read-set becomes `consumes`;
   what it wrote becomes `call.effects.create/update/link(...)`. A node that
   only computes a value creates an artifact instead of returning it.
3. **Drop the edges.** You rarely declare transitions — an agent wakes when its
   consumed artifacts exist. Use `Consume.by_status`/`by_field` for a
   conditional edge.
4. **Replace mutation with effects.** Any `state["k"] = v` becomes
   `call.effects.create(...)` or `update(artifact, field=v)`.
5. **Replace messages with artifacts.** Conversation turns are `Question` →
   `Answer` artifacts on one shared context; keep raw transcripts only where a
   model needs them (`ChatMemory`).
6. **Test with `ScenarioLab`** (see the `reactifact-testing` skill) and score
   with `reactifact.eval`.

## From a mutable-state node

```python
# not-run: the LangChain side is not installed — shown for contrast
# LangGraph: mutate a shared state dict
def node(state):
    state["answer"] = call_model(state["question"])
    return state
```

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Consume, Context, Produce, Runtime, RuntimeResources, create_agent,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        # instead of mutating state: declare the change
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


agent = create_agent("answerer", consumes=[Consume(Question)], produces=[Answerer()])
ctx = Context(resources=RuntimeResources())
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=[agent]).arun())
assert ctx.latest(Answer) is not None
```

## What you gain

- **Provenance by construction** — answers link to evidence; `reactifact.eval`
  can verify grounding structurally.
- **Determinism** — the runtime is a fixpoint over triggers; replay and golden
  tests are exact.
- **No framework lock-in on models** — a provider-agnostic `LLMProvider`, and a
  `FakeLLM`/`ReplayLLM` for tests.

## Where to look

- `docs/en/comparison.md` and `docs/en/why-reactifact.md` for the framing.
- `docs/en/concepts.md`, `docs/en/recipes.md` for idioms.
- The other reactifact skills for writing (`reactifact`), testing
  (`reactifact-testing`) and evaluating (`reactifact-eval`).
