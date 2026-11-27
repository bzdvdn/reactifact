# Why reactifact — the idea in one page

This is the *design argument*, not a feature list. If you already know what
reactifact does, this page explains the *why*: why state instead of execution,
why typed artifacts instead of message strings, why versioned history, and why
determinism is a philosophy rather than an option. The invariants behind every
claim live in [constitution.md](../constitution.md).

## If you know Celery, you already know the model

Celery's mental model: define a **task**, declare what triggers it, let the
runtime run it; results land in a result backend. reactifact applies exactly that
to agents:

- a **task** is a `Step` / `@step(produces=Model, reads=Input)` — a unit of work that writes an artifact;
- the **trigger** isn't a message you push — creating the input artifact *is*
  the trigger, and `Consume(Type)` says which artifact type wakes the task;
- a **chain / group / chord** is several step `Consume`s / `produces`; the
  runtime derives the order from state, not from wiring;
- the **result backend** is the `Context` — typed, versioned artifacts, so you
  get provenance and reproducibility a task queue doesn't give you.

One honest caveat: reactifact is single-process today — a Celery-shaped *model*,
not its distributed broker/worker runtime. The rest of this page argues *why*
that model (state over execution) is the right one for open-ended knowledge work.

## The problem with "agent = a graph you draw"

Most agent frameworks give you a **graph** (or a chain) as the primary
abstraction: you connect nodes, you wire memory, you declare control flow.
This works when the task is a fixed pipeline — but knowledge agents are not.

A user asks *"Why did infrastructure costs increase in Q2?"* (CONSTITUTION §1.1).
The answer may need Confluence + GitLab MRs + CSV + calculations + verification
+ follow-up questions. The next question needs a *different* path. There is no
universal fixed graph — and asking the developer to draw one for every possible
question is asking them to predict the future.

**reactifact flips the model.** You do not describe execution. You describe
*what data exists, what artifacts exist, and what agents can do with them*; the
runtime derives what can run next **from state changes**. Agents react. There is
no graph to draw.

## What you get instead

| Property | What it means in practice |
| --- | --- |
| **Typed state** | A turn is not a chat string; it is `Claim`, `Evidence`, `Answer`, `Calculation` — versioned objects with a schema. |
| **Provenance by construction** | Every derived artifact links back to what produced it, so an answer can be audited without reading the prompt. |
| **Rewindable history** | Every run is a commit: `diff`, `checkout()`/`at()`, `branch()`, `merge()`, deterministic replay. |
| **Reactive scheduling** | The next step is chosen from state changes; adding a new capability does not mean redrawing a graph. |
| **Determinism where it pays** | Calculations stay in code; the LLM is used for language and judgement, not arithmetic or lookup. |
| **Production controls** | Budgets, retries, guardrails, authorization, human approval and quotas are part of the runtime, not per-app glue. |
| **Observability** | Traces, spans, tool calls, token/cost stats, metrics and online evaluation are available for every run. |
| **Testability** | `ScenarioLab`, `FakeLLM`, record/replay and `test_client()` make a run deterministic and inspectable. |
| **A thin app layer** | `Pipeline` for one request, `Chat` for a conversation, `@tool` for capabilities — with no framework app to adopt. |
| **No lock-in** | Core is dependency-light; FastAPI, Postgres, MCP and vector stores are optional extras, and low-level primitives remain public. |

## Why artifacts, not messages

In a message-style agent, turns are strings — you see the text and nothing else.
Artifacts are different: **typed, versioned, provenance-aware objects**
(`Claim`, `Evidence`, `Answer`, `Calculation`). Because every derived artifact
links back to what produced it, both the runtime and your users can figure out
*where an answer came from* — just walk the links:

```text
Answer —supported_by→ Claim —derived_from→ Evidence —extracted_from→ Doc
```

That is not a nice-to-have; it is the entire point of an agent that will be
held accountable (§15, §34).

The authoring shape follows from that. A step declares what it reads and writes,
and returns a typed artifact or writes one through the runtime:

```python
@step(reads=Question, produces=Answer)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())
```

The runtime validates the result against `produces`, commits it with its
provenance, and wakes the next step. There is no hand-built patch and no
half-applied state.

## Why versioned state

Every run is a commit. You get `diff`, `checkout()`/`at()`, `branch()` and `merge()` for
the state of a conversation (§39–§40) and deterministic replay (§55). This is
the difference between *a transcript you can look at* and *a history you can
rewind, fork and safely merge*.

## Why determinism is a philosophy, not a default

The dominant pattern is "let the LLM do everything". reactifact takes the
opposite stance (§67):

- **Calculations are calculated.** `Spreadsheet → Calculation` over CSV is
  deterministic code, not a hallucinated number.
- **Eligibility is a decision of the state.** A step's `reads`/`When`
  selectors decide *whether* it reacts; only genuinely generative steps call
  the LLM.
- **Honesty beats pretending.** On failure a step returns `None` (or yields
  nothing) rather than a confident guess (§59). The model is a reasoning
  component, never the source of truth.

The rule of thumb: *if it can be computed, compute it; the LLM only does what
requires language and judgement.*

## Why the runtime, not "the app wires everything"

In a traditional framework the app passes messages between components by hand.
Here the app declares what agents **consume** and **produce**; the runtime
matches events to consumers, collects inputs, runs steps, commits changes
atomically, records reads/writes/relations, and wakes the next generation.
Orchestration "falls out of the state" — you spend your time on the domain, not
on plumbing.

## The event loop — how "reaction" actually happens

The reactive engine is a **single, ordinary loop**; there is no node scheduler
to configure. What agents *react to* is not a graph edge — it is an event, and
the loop is this:

1. Applying a commit emits **`Event`s** — `artifact_created`,
   `artifact_updated`, `artifact_deleted` — each referencing an artifact by
   type and id (CONSTITUTION Stage 4, "Reactive Runtime").
2. The runtime **drains** those events (`arun_once` → `drain_events()`) and,
   for each event, asks every agent *do you match?* (`agent.matches(event)`).
3. Matching is exactly your **`Consume`** declarations — an artifact type and an
   optional `condition` (e.g. `Consume.by_status(...)`). No central registry,
   no wiring table: the reaction set *is* the declaration.
4. Matching agents run (respecting budget, concurrency, priority); their
   changes become the **next** atomic commit; the loop turns again — until a
   generation produces nothing and the run settles.

Two details make this useful rather than merely clever:

- **Events are derived, not handwritten.** You never emit an event yourself;
  creating/updating/deleting an artifact *is* the event, and the commit yields
  them mechanically (§41). The causal chain can never drift from the actual
  state, because it *is* the state's changelog.
- **The trigger reaches the step.** A class step can take a typed
  `Artifact[T]` (or the compatibility `trigger` parameter) and react to *which*
  artifact changed; `inputs`/`list[Artifact[T]]` gives the full consumed set.

This is why "reactive" is not marketing: the *only* way control flows in
reactifact is through changes to state, and events are precisely the change that
agents see. No `if x: call y` plumbing — the state decides, the loop follows.

## The mental model

| Layer | What it is | Who writes it |
| --- | --- | --- |
| **Step** | the reaction: guard → LLM/calculation → a typed result | you |
| **Artifact** | typed, versioned state with provenance | you, through the step's result |
| **Runtime** | matching, atomic commit, events, traces, retries | the framework |

## A FastAPI-shaped developer experience

The same model does not require framework ceremony. `Pipeline` is the facade for
one run, `Chat` for a conversation:

```python
from reactifact import Chat, Agent, Pipeline, PipelineRunner

pipeline = Pipeline(
    agents=[Agent("answer", steps=[search, answer])],
    seed_type=Question,
    answer_type=Answer,
    traces=True,
)
result = PipelineRunner(pipeline).run(Question(text="why did costs grow in Q2?"))

chat = Chat(
    agents=[Agent("answer", steps=[search, answer])],
    store="sessions.db",
    seed_type=Question,
    answer_type=Answer,
)
reply = await chat.invoke("and last month?", session_id="u1")
```

Tools follow the same typed, injected style — the model sees the operation, the
app supplies the client:

```python
@tool(retry=RetryPolicy(max_attempts=3), timeout=10)
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    return await k8s.get(name)
```

`Pipeline.test_client()`, `Chat.test_client()`, `ScenarioLab` and `FakeLLM`
let you test the same code without a network. `Chat.router()` mounts the SSE
contract on FastAPI when you need HTTP; the core does not depend on it.

## Does this mean "no graphs ever"?

No — and the docs should not claim that. There is always a dependency graph
under the hood: steps are nodes, artifact versions and `reads` are edges, and the
runtime walks it. The difference is **authorship**: LangGraph asks you to draw
that graph; reactifact derives it from typed step inputs and the committed
artifacts, so it cannot drift from the code. The claim is "no graph to draw",
not "no graph".

Where a workflow is genuinely fixed, reactifact still expresses it — but the
point is you are **not forced** to draw a graph when the problem is open-ended.
Many canonical patterns (ReAct, RAG, branch/merge, staged replanning) have
reference implementations in [`examples/`](examples.md) (see
[port-matrix](port-matrix.md)); they come out of *state and produces*, not out
of a pre-drawn diagram.

## Where reactifact will not be a fit

- **Scripted pipelines** — if the task is a truly fixed sequence of steps with
  no room for "reaction", you do not need an agent framework at all: a plain
  function suffices, and if you want explicit transitions, a classic graph
  works fine. Pick the tool that fits the job.
- **Pure playgrounds** — if you only want the model to "figure it out" and you
  do not care about determinism, provenance or accountability, reactifact adds
  ceremony you can skip.

Everything else — agents that gather evidence, verify claims, calculate,
respect budgets, ask humans, and can be audited — is exactly what reactifact
was built for.
