---
name: reactifact
description: Build, extend, refactor, review or debug Python agents with reactifact, the artifact-driven reactive runtime. Use this whenever the user mentions reactifact, artifacts, a Context, Step/Consume, Effects, provenance, a reactive agent graph, or asks for an agent pipeline, recipe or multi-agent flow in Python — even when they do not name the framework. This is the entry point of the reactifact skill pack; it holds the mental model and routes to the focused skills (agents, llm, rag, testing, eval, observability, migration). Do not use it for generic Python questions that involve no agent pipeline.
---

# reactifact — the mental model and the working loop

reactifact is a reactive runtime over **typed, versioned, provenance-aware
artifacts**. There is no imperative orchestration and no mutable state dict. A
step declares the artifacts it reads (`reads`) and may produce
(`produces`); it writes changes as **effects**; the runtime compiles a turn's
effects into one atomic patch and loops until nothing new can run. The graph of
artifacts *is* the program state.

Read this skill first, then load only the focused skill the task needs (see the
routing table below). Loading every skill wastes context and blurs the rules.

**When not to use it.** A fixed A → B → C chain, a one-off script, or a single
function call does not need a runtime: plain Python (or LangGraph) is simpler.
reactifact earns its keep when state is shared across steps, must be auditable,
or must be re-derived after a crash. Don't wrap every script in an `Agent`.

## The three invariants

Everything else follows from these. Violating one is what makes an LLM's
reactifact code look like LangChain wearing a costume.

1. **State changes only through effects (or a returned model).** Prefer
   returning a model or `Emit`/`Ensure`/`Edit`/`Drop`/`Relate`; when a step
   needs imperative writes, take `effects: Effects` and use
   `effects.create/update/link/...`. Never assign to the context or mutate
   `artifact.data`. The runtime commits the effect set as one atomic patch.
2. **A step declares its inputs and outputs.** `Read(Question)` / typed
   parameters are the step's inputs; `produces=(Answer,)` is the allowed
   create set. An `Agent` owns `consumes`; its steps read what they need and
   may add read-only inputs without waking the agent.
3. **Everything is a typed artifact with provenance.** Answers link to their
   evidence (`answer.link("supported_by", evidence)`); downstream code and
   `reactifact.eval` trust the graph, not the prose.

## Pick the primitive, not the workaround

Start at the top; only move down when the higher-level form genuinely cannot
express the task. Most "framework friction" is reaching for `Context`, a
hand-built `Patch`, or an imperative `Agent.run` when a typed step would
do.

1. **`@step` / `Step` with typed inputs.** Declare `reads`/`produces`; return
   a model or `Emit`/`Ensure`/`Edit`/`Drop`/`Relate`, or write `effects`.
   `scope=` and `Unscoped()` cover correlation and cross-turn history;
   `guard=` adds a pre/post run condition; `ArtifactKey` names a stable id;
   `CurrentRequest`, `Progress`, `Human` and `Outbox` are injected by type.
   Use `@agent` for a one-function agent, or `Agent(consumes=[...],
   steps=[...])` for several.
2. **`Consume` / `Read` lifecycle forms** for event-shaped work:
   `Consume.on_create`/`on_update`/`on_stale`/`on_delete` on the agent;
   `Read.on_create`/`on_update`/`on_stale`/`after_delete` on the step.
   `Read.idempotent`/`Read.join`/`Read.correlated` mirror the advanced
   consume variants.
3. **A recipe** when the flow is a known pattern: `TurnContextStep` for a
   conversation window + digest, `Route` (pass it in `agents=[route, ...]`) for
   mutually exclusive cases, `ApprovalGate`, `ReflectionLoop`, `PlanExecute`,
   `StatusMachine`, `WindowPruner`, `materialize_doc`. Use `Verify(Answer)` for
   live quality checks without a dummy `Step(...)`.
4. **Low-level API only when the flow is genuinely imperative:** a hand-written
   `Agent.run(event, context) -> Patch`, direct `Context` queries that
   cannot be expressed as typed inputs, or a custom `Patch` for an integration.
   This is an escape hatch, not the authoring style; document why in a
   comment.

The same ladder applies to conversations: prefer `TurnContextStep` +
`Consume(TurnContext)` over every step taking `Context` and rebuilding the
transcript. Inside a step use `Conversation.add_effect(effects, ...)` (not
`add`, which writes directly); `window=0` renders an empty window when you
deliberately want no history.

## Canonical example

The smallest end-to-end pipeline: seed a `Question`, a step reads it and
creates an `Answer`, the runtime runs to a fixpoint.

```python
import asyncio
from pydantic import BaseModel
from reactifact import Agent, Context, Runtime, step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(reads=Question, produces=Answer)
async def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


AnswerAgent = Agent("answerer", consumes=[Question], steps=[answer])


ctx = Context()
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=[AnswerAgent]).arun())

assert ctx.latest(Answer).data.text == "HELLO"
```

`arun()` runs to a fixpoint: it re-schedules steps whose consumed artifacts
exist until nothing new is produced. `run()` and `run_once()` are the sync /
single-pass variants.

## Choose the entry point

| Need | Use | Why |
| --- | --- | --- |
| one run of a pipeline (script, job, service) | `Pipeline(agents=[...])` + `PipelineRunner` | `Pipeline` owns structure; the runner plus `RunConfig` owns resources/budget/tracing and `run/arun`/`answer()` |
| a persisted multi-turn chat | `Chat(agents=[...], seed_type=..., answer_type=...)` with `config=RunConfig(...)`, or `Chat(pipeline=...)` | sessions, `invoke/stream/history/answer`, `router()`, `test_client()`, shared run config; pass `Invocation` for retry-safe turns |
| ordered stages with barriers | `StagedPipeline(stages=[Stage(...)])` | agents stay reactive inside a stage; the next stage starts at quiescence; `Stage(reopen=True)` allows re-entry |
| a custom `Context`/`Session`/scheduler | `Runtime(ctx, steps=... \| agents=...)` | full control; `RunResult.trace()` plus `trace_id`/`trace_store` stay available |

For example, the same `answer` step as a one-shot pipeline:

```python
# not-run: illustrative — answer/Question/Answer from the example above
from reactifact import Agent, Pipeline, PipelineRunner

pipeline = Pipeline(
    agents=[Agent("answer", consumes=[Question], steps=[answer])],
    seed_type=Question,
    answer_type=Answer,
)
result = PipelineRunner(pipeline).run(Question(text="hello"))
assert result.context.latest(Answer).data.text == "HELLO"
```

`Chat` is the FastAPI-shaped facade for conversations; `Pipeline` is the
declarative definition for one request and `PipelineRunner` executes it. Reach
for `Runtime` only when you need the session, a scheduler, or a long-lived
context.

Inspect the declared surface — steps, reads/produces, tools, timeout/retry —
with `reactifact contracts <module>` (Markdown/JSON), or mount
`create_contracts_router(agents)` from `reactifact.contracts.web` as an opt-in
FastAPI page (`/contracts`, `/api/contracts`, `/contracts.md`).

## The working loop

Follow this order for any non-trivial task. Each step has an exit criterion —
do not skip ahead, because a wrong artifact model is expensive to unwind.

1. **Design the artifacts.** Name the `BaseModel` types and the provenance
   edges between them. Exit when every piece of state the user mentioned is a
   type, and every "because/which supports" is a relation.
2. **Design the steps.** For each transformation: what it reads, what it
   produces. Exit when inputs and outputs are artifacts.
3. **Implement the steps.** Effects or a returned model; deterministic logic in
   Python, the LLM only for reasoning. Exit when no step mutates and every
   answer links its evidence. → `reactifact-agents`, `reactifact-llm`
4. **Wire and run.** Bundle steps into an `Agent`, build
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
| Write/review steps, reads, effects, provenance | `reactifact-agents` | `reactifact-agents/references/consume.md`, `reactifact-agents/references/effects.md` |
| Add an LLM step, structured output, tools, budget | `reactifact-llm` | `reactifact-llm/references/providers.md`, `reactifact-llm/references/tools.md` |
| Retrieve from files/CSV/web/knowledge, RAG, fan-out | `reactifact-rag` | `reactifact-rag/references/sources.md` |
| Guardrails, authorization/scopes, per-tenant quota | this skill | `reactifact/references/trust-and-safety.md` |
| Test/debug a pipeline deterministically | `reactifact-testing` | — |
| Score quality over a dataset, CI gate | `reactifact-eval` | — |
| Traces, metrics, sessions, durability, CLI/viz | `reactifact-observability` | — |
| Call or serve another agent over A2A (Agent2Agent) | `reactifact.a2a` (module, not a skill) | `docs/en/a2a.md` |
| Port LangChain/LangGraph code | `reactifact-from-langchain` | — |

The `docs/en/...` paths point into the reactifact repository (or the docs site
https://bzdvdn.github.io/reactifact/): an installed skill bundle carries only
the skills and their `references/`, not the docs tree.

Docs (when the skills are not enough): `docs/en/concepts.md`,
`docs/en/effects.md`, `docs/en/recipes.md`, `docs/en/providers.md`,
`docs/en/testing.md`, `docs/en/eval.md`, `docs/en/observability.md`.

## Top mistakes (and the structural fix)

| Wrong | Right |
| --- | --- |
| `context.state["x"] = v`, or mutating `artifact.data` | return a model/`Emit`, or `effects.create/update(...)` |
| treating a step's return value as a function result | return the model/`Emit` — the runtime commits it as an artifact; downstream steps read it |
| calling another agent/step directly | declare `Agent.consumes` and step `reads`/`produces`; let the runtime schedule |
| a fresh `Runtime` per request with an empty context | seed the context; keep one session across turns |
| state in a module global or a plain dict | make it an artifact (typed, versioned, traced) |
| free-text answer with no links | create source artifacts and `link("supported_by")` |
| a manual `isinstance(trigger...)` guard | declare `Agent.consumes` or `Annotated[T, When(...)]` |
| `Agent(steps=[...])` with no `consumes` | spell `consumes=[...]` out; dev mode warns because the derived wake set hides the scheduling contract |
| guessing an API from memory | check it: `python -c "import reactifact; help(...)"` or the docs |

## Glossary

- **Artifact** — an immutable, typed snapshot in the `Context`
  (`ctx.latest(T) -> Artifact[T]`; `.data` is your pydantic model, `.id`,
  `.version`, `.content_hash`). History lives in the context store.
- **Context** — the queryable artifact graph (`latest`, `list_artifacts`,
  `related`, `relations`, `get`, `artifact_version`).
- **Step** — the unit of transformation: `reads`/`produces`, `run()` or a
  `@step` function; return a model for create-sugar or write `effects`.
- **Agent** — the scheduling unit: owns `consumes` and an ordered list of
  `steps`.
- **Read** — a step input (`Read(Question)`, `Read.on_create`,
  `Read.after_delete`, `Read.idempotent`, ...). It never wakes the agent.
- **Consume** — the agent wake contract; lifecycle forms are
  `Consume.on_create`/`on_update`/`on_stale`/`on_delete`.
- **Unscoped** — `Annotated[T, Unscoped()]` reads a type across every turn
  instead of only the current `scope`; combine with `Wakes(False)` for history.
- **Effects** — the step-scoped change set; compiled into one `Patch`.
- **Progress** — an ephemeral UI status from a step (`ProgressEvent`, never
  committed); inject `progress: Progress` or use `self.progress`.
- **CurrentRequest** — read-only `Mapping` of `Runtime.arun(request=...)`,
  injected by type.
- **TurnContext** — a recipe artifact with the current query, previous query,
  window and digest, so downstream steps need no `Context`.
- **Relation** — a typed provenance edge (source —relation→ target).

## Self-check before you are done

1. No step assigns to `context` or to `.data` — everything goes through
   `effects` or a returned model/`Emit`. (Search your diff for `= ` on
   context/artifacts.)
2. Every terminal artifact (answer/report/decision) has a provenance edge to
   what justifies it.
3. Every step's inputs and outputs are artifacts (or it is an explicit
   side-effect-only step with `produces=()` / `deletes=(...)`), and `Runtime`
   is built once with the full agent/step list.
4. Tests assert on artifacts, the path, and relations — not on returned values
   — and pass with no network (`FakeLLM`/`ReplayLLM` or no LLM at all).
5. You verified the real API of anything you were unsure about; you did not
   invent a signature.

## Where to look

- `references/effects-and-state.md` — reading state, ids, provenance,
  multi-turn sessions, determinism.
- The focused skills above, and the bundled examples in `examples/`
  (`knowledge`, `repair`, `devops`, `forklab`, `fintech_audit`, `chat`).
