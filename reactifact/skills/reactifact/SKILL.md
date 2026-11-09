---
name: reactifact
description: Build, extend, refactor, review or debug Python agents with reactifact, the artifact-driven reactive runtime. Use this whenever the user mentions reactifact, artifacts, a Context, Step/Consume, Effects, provenance, a reactive agent graph, or asks for an agent pipeline, recipe or multi-agent flow in Python — even when they do not name the framework. This is the entry point of the reactifact skill pack; it holds the mental model and routes to the focused skills (agents, llm, rag, testing, eval, observability, migration). Do not use it for generic Python questions that involve no agent pipeline.
---

# reactifact — the mental model and the working loop

reactifact is a reactive runtime over **typed, versioned, provenance-aware
artifacts**. There is no imperative orchestration and no mutable state dict. A
step declares the artifacts it consumes (`consumes`) and may produce
(`produces`); it writes changes as **effects**; the runtime compiles a turn's
effects into one atomic patch and loops until nothing new can run. The graph of
artifacts *is* the program state.

Read this skill first, then the focused skill for the task at hand (see the
routing table below).

## The three invariants

Everything else follows from these. Violating one is what makes an LLM's
reactifact code look like LangChain wearing a costume.

1. **State changes only through effects (or a returned model).** A class step
   declares `effects: Effects` in `run()`; a function step uses
   `call.effects`. Never assign to the context or mutate `artifact.data`.
   The runtime commits the effect set as one atomic patch.
2. **A step declares its contract.** `consumes = (Question,)` feeds typed
   inputs and decides which events run it; `produces = (Answer,)` is the
   allowed create set. An `Agent` groups steps; triggers derive from their
   `consumes`.
3. **Everything is a typed artifact with provenance.** Answers link to their
   evidence (`answer.link("supported_by", evidence)`); downstream code and
   `reactifact.eval` trust the graph, not the prose.

## Canonical example

The smallest end-to-end pipeline: seed a `Question`, a step consumes it and
creates an `Answer`, the runtime runs to a fixpoint.

```python
import asyncio
from pydantic import BaseModel
from reactifact import Agent, Context, Effects, Runtime, Step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question, effects: Effects) -> None:
        effects.create(Answer(text=question.text.upper()))


class AnswerAgent(Agent):
    steps = [Answerer()]


ctx = Context()
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=[AnswerAgent()]).arun())

assert ctx.latest(Answer).data.text == "HELLO"
```

`arun()` runs to a fixpoint: it re-schedules steps whose consumed artifacts
exist until nothing new is produced. `run()` and `run_once()` are the sync /
single-pass variants.

## The working loop

Follow this order for any non-trivial task. Each step has an exit criterion —
do not skip ahead, because a wrong artifact model is expensive to unwind.

1. **Design the artifacts.** Name the `BaseModel` types and the provenance
   edges between them. Exit when every piece of state the user mentioned is a
   type, and every "because/which supports" is a relation.
2. **Design the steps.** For each transformation: what it consumes, what it
   produces. Exit when inputs and outputs are artifacts.
3. **Implement the steps.** Effects or a returned model; deterministic logic in
   Python, the LLM only for reasoning. Exit when no step mutates and every
   answer links its evidence. → `reactifact-agents`, `reactifact-llm`
4. **Wire and run.** Group steps into an `Agent`, build
   `Runtime(ctx, agents=[...])`, seed inputs, `arun()`. Exit when the run
   reaches a fixpoint and produces the target artifact.
5. **Test, then evaluate.** Assert on the produced graph with `ScenarioLab`
   (deterministic, no network), then measure quality over a dataset.
   → `reactifact-testing`, `reactifact-eval`.

For an existing codebase, do steps 1–2 as a review first: find the places that
mutate state or call agents directly, and turn those into the model above —
`reactifact-from-langchain` has the mapping.

## Routing table

| Task | Skill | Key reference |
| --- | --- | --- |
| Write/review steps, consumes, effects, provenance | `reactifact-agents` | `references/consume.md`, `references/effects.md` |
| Add an LLM step, structured output, tools, budget | `reactifact-llm` | `references/providers.md`, `references/tools.md` |
| Retrieve from files/CSV/web/embeddings, RAG, fan-out | `reactifact-rag` | `references/sources.md` |
| Guardrails, authorization/scopes, per-tenant quota | this skill | `references/trust-and-safety.md` |
| Test/debug a pipeline deterministically | `reactifact-testing` | — |
| Score quality over a dataset, CI gate | `reactifact-eval` | — |
| Traces, metrics, sessions, durability, CLI/viz | `reactifact-observability` | — |
| Call or serve another agent over A2A (Agent2Agent) | `reactifact.a2a` | `docs/en/a2a.md` |
| Port LangChain/LangGraph code | `reactifact-from-langchain` | — |

Docs in the repo (when the skills are not enough): `docs/en/concepts.md`,
`docs/en/effects.md`, `docs/en/recipes.md`, `docs/en/llm.md`,
`docs/en/testing.md`, `docs/en/eval.md`, `docs/en/observability.md`.

## Top mistakes (and the structural fix)

| Wrong | Right |
| --- | --- |
| `context.state["x"] = v`, or mutating `artifact.data` | `effects.create/update(...)` / `call.effects` |
| returning a value from a step to hand back a result | create an artifact; the next step consumes it |
| calling another agent/step directly | declare `consumes`/`produces`; let the runtime schedule |
| a fresh `Runtime` per request with an empty context | seed the context; keep one session across turns |
| state in a module global or a plain dict | make it an artifact (typed, versioned, traced) |
| free-text answer with no links | create source artifacts and `link("supported_by")` |
| a manual `isinstance(call.trigger...)` guard | declare `consumes` / `Annotated[T, When(...)]` |
| guessing an API from memory | check it: `python -c "import reactifact; help(...)"` or the docs |

## Glossary

- **Artifact** — an immutable, typed snapshot in the `Context`
  (`ctx.latest(T) -> Artifact[T]`; `.data` is your pydantic model, `.id`,
  `.version`, `.content_hash`). History lives in the context store.
- **Context** — the queryable artifact graph (`latest`, `list_artifacts`,
  `related`, `relations`, `get`, `artifact_version`).
- **Step** — the unit of transformation: `consumes`/`produces`, `run()`;
  return a model for create-sugar or write `effects`.
- **Agent** — a thin container of `steps`; triggers derive from their
  `consumes`.
- **Consume** — a subscription to artifacts of a type (see `reactifact-agents`
  for the variants).
- **Effects** — the step-scoped change set; compiled into one `Patch`.
- **Relation** — a typed provenance edge (source —relation→ target).

## Self-check before you are done

1. No step assigns to `context` or to `.data` — everything goes through
   `effects`/`call.effects`. (Search your diff for `= ` on context/artifacts.)
2. Every terminal artifact (answer/report/decision) has a provenance edge to
   what justifies it.
3. Every step's inputs and outputs are artifacts, and `Runtime` is built once
   with the full agent list.
4. Tests assert on artifacts, the path, and relations — not on returned values
   — and pass with no network (`FakeLLM`/`ReplayLLM` or no LLM at all).
5. You verified the real API of anything you were unsure about; you did not
   invent a signature.

## Where to look

- `references/effects-and-state.md` — reading state, ids, provenance,
  multi-turn sessions, determinism.
- The focused skills above, and the bundled examples in `examples/`
  (`knowledge`, `repair`, `devops`, `forklab`, `fintech_audit`, `chat`).
