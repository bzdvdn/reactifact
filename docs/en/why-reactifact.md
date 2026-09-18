# Why reactifact — the idea in one page

This is the *design argument*, not a feature list. If you already know what
reactifact does, this page explains the *why*: why effects instead of graphs,
why versioned state, why determinism is a philosophy rather than an option.
The invariants behind every claim live in [constitution.md](../constitution.md).

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

## Why `self.effects` instead of "return a patch"

In many frameworks a unit of work *returns* its change (a tool call, a message,
a dict) and some orchestrator applies it. reactifact inverts authoring (§24):

```python
async def produce(self, call: ProduceCall) -> None:
    evidence = self.effects.create(Evidence(...), id="evidence:q1")
    answer = self.effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)
    answer.link("supported_by", evidence)
    self.effects.update(turn, status="answered")
    return None
```

Why this shape?

- **The author writes intent, not wiring.** `self.effects` reads like a
  sentence: *create this, link that, bump the status*. You never assemble the
  `Patch` — the runtime does.
- **You work with artifacts, not ids.** `evidence` is created here and linked a
  line below — the same object, no "look it up by number" step. The code speaks
  about artifacts, not identifiers.
- **One atomic commit.** Nothing applies until the produce returns (§41). No
  "half applied" states, no manual rollback ladder. Events, validation against
  `produces`, and the trace are all derived from the same compiled ops.
- **Parallel-safe by construction.** Each produce gets its own slot; concurrent
  producers never see each other's half-built state (§42).
- **HITL falls out.** `self.effects.ask(...)` is just another effect (§60); the
  human is not a special case bolted on the side.

If you find yourself writing `Patch()` inside a produce — stop. That is the
runtime's job.

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

## Why versioned context

Every run is a commit. You get `diff`, `rollback`, `branch()` and `merge()`
for the state of a conversation (§39–§40) and deterministic replay (§55). This
is the difference between *a transcript you can look at* and *a history you can
rewind, fork and safely merge*.

## Why determinism is a philosophy, not a default

The dominant pattern is "let the LLM do everything". reactifact takes the opposite
stance (§67):

- **Calculations are calculated.** `Spreadsheet → Calculation` over CSV is
  deterministic code, not a hallucinated number.
- **Eligibility is a decision of the state.** A produce's guard decides
  *whether* it reacts; only genuinely generative steps call the LLM.
- **Honesty beats pretending.** On failure a produce returns `None` (or yields
  nothing) rather than a confident guess (§59). The model is a reasoning
  component, never the source of truth.

The rule of thumb: *if it can be computed, compute it; the LLM only does what
requires language and judgement.*

## Why the runtime, not "the app wires everything"

In a traditional framework the app passes messages between components by hand.
Here the app declares what agents **consume** and **produce**; the runtime
matches events to consumers, collects inputs, runs produces, compiles the
effects into one atomic patch, records reads/writes/relations, and wakes the
next generation. Orchestration "falls out of the state" — you spend your time
on the domain, not on plumbing.

## The event loop — how "reaction" actually happens

The reactive engine is a **single, ordinary loop**; there is no node scheduler
to configure. What agents *react to* is not a graph edge — it is an event, and
the loop is this:

1. Applying a patch emits **`Event`s** — `artifact_created`,
   `artifact_updated`, `artifact_deleted` — each referencing an artifact by
   type and id (CONSTITUTION Phase 4, "Reactive Runtime").
2. The runtime **drains** those events (`arun_once` → `drain_events()`) and,
   for each event, asks every agent *do you match?* (`agent.matches(event)`).
3. Matching is exactly your **`Consume`** declarations — an artifact type and an
   optional `condition` (e.g. `Consume.by_status(...)`). No central registry,
   no wiring table: the reaction set *is* the declaration.
4. Matching agents run (respecting budget, concurrency, priority); their
   effects compile into the **next** atomic patch; the loop turns again — until
   a generation produces nothing and the run settles.

Two details make this useful rather than merely clever:

- **Events are derived, not handwritten.** You never emit an event yourself;
   creating/updating/deleting an artifact *is* the event, and the commit yields
   them mechanically (§41). The causal chain can never drift from the actual
   state, because it *is* the state's changelog.
- **`event` reaches the produce.** A produce signature is
   `produce(self, context, inputs, event=None)`: it can react *specifically to
   what changed* (which artifact id/type) while `inputs` gives the full window
   of the consumed type.

This is why "reactive" is not marketing: the *only* way control flows in
reactifact is through changes to state, and events are precisely the change that
agents see. No `if x: call y` plumbing — the state decides, the loop follows.

## The three-layer mental model

| Layer | What it is | Who writes it |
| --- | --- | --- |
| **Produce** | the reaction: guard → LLM/calc → `self.effects.*` → `None` | you |
| **Effects** | the stated change-set, scoped to the turn | you, via `self.effects` |
| **Patch** | the *compiled* operations applied as one commit | the runtime |

## Why even the app-facing layer is thin

`ChatAssistant` (sessions + turns + history) and `create_chat_router`
(SSE contract on your FastAPI) cover the canonical chat without forcing you
into a framework-app. You keep your session store, your resources, your
artifacts; the framework provides the loop. If your loop differs — parallel
forks, a steering second endpoint — the building blocks (`run_message`,
`create_agent`, effects) compose under it.

## Does this mean "no graphs ever"?

No. Where a workflow is genuinely fixed, reactifact still expresses it — but the
point is you are **not forced** into a graph when the problem is open-ended.
The canonical patterns (ReAct, reflection, map-reduce, supervisor, summarize,
time-travel, plan-and-execute) all have reference implementations in
[`examples/`](examples.md) (see [port-matrix](port-matrix.md)); they come
out of *state and produces*, not out of a pre-drawn diagram.

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