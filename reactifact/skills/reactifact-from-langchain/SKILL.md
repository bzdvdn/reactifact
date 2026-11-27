---
name: reactifact-from-langchain
description: Migrate LangChain or LangGraph agent code to reactifact, and translate between the two mental models. Use whenever the user has a LangChain/LangGraph chain, StateGraph, tool-calling agent or message-list memory and wants it in reactifact (or asks how the paradigms map), or is porting a RAG or multi-agent app off LangChain. Not for greenfield reactifact design (use `reactifact`).
---

# Migrating from LangChain / LangGraph to reactifact

The core shift: LangGraph passes a mutable **state dict** between nodes you run
by hand; reactifact has **typed artifacts** and a runtime that schedules agents
when their inputs appear. There is no `state` to thread — you create artifacts
and the graph grows.

## Concept map

| LangChain / LangGraph | reactifact |
| --- | --- |
| `StateGraph`, `add_node`, `add_edge` | `Agent(name, steps=[...])` — edges are implicit in step `reads`/`produces` |
| mutable state dict + reducers | typed `BaseModel` artifacts in a `Context` |
| `RunnableLambda` / a chain step | a `Step` (an agent's transformation) |
| `@tool` / `Tool` | `reactifact.tools.tool` / `Tool` / `FunctionTool` |
| tool-calling loop (`AgentExecutor`) | `LLMAgent` (system + tools) plus `steps=[...]` to turn the final `ToolAnswer` into domain artifacts, or `ToolUse` / `NativeToolUse` |
| `MemorySaver` / checkpointer | `Session` + a checkpoint/KV store |
| `messages: list[...]` as memory | artifacts (`Question`/`Answer`/…) across turns, or `ChatMemory` |
| conditional edges | `Consume.by_status(...)` / `by_field(...)`, absent-consume gates; `recipes.Route` when exactly one branch must run (`route.agents()`) |
| `app.invoke(...)` | `Runtime(ctx, agents=[...]).arun()` |
| LangSmith traces | `reactifact.tracing.TraceStore` (`tracer=[...]`) |
| `langchain.evaluation` / LangSmith evals | `reactifact.eval` (`docs/en/eval.md`) |

## Porting recipe

1. **Name the state.** Every key the graph's state dict carries becomes a
   `BaseModel` artifact type.
2. **Turn each node into a step.** The node's read-set becomes the step's
   `reads` (or typed parameters); what it wrote becomes a returned model or
   `effects.create/update/link(...)`. A node that only computes a value still
   has that value become an artifact, never a plain return value to the caller.
3. **Drop the edges.** You rarely declare transitions — an agent wakes when its
   `consumes` fire. Use `Consume.by_status`/`by_field` for a conditional edge,
   and `Read` for inputs a step needs without waking the agent again.
4. **Replace mutation with effects or a returned model.** Any `state["k"] = v`
   becomes a returned model, `effects.create(...)`, or `Edit(artifact, field=v)`.
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
    Agent, Context, Runtime, RuntimeResources, step,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(reads=Question, produces=Answer)
async def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


AnswerAgent = Agent("answer_agent", consumes=[Question], steps=[answer])
agent = AnswerAgent
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
