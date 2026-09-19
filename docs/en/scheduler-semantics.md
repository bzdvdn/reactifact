# Scheduler semantics — the execution contract

This page is the **execution contract** of `Runtime`: how a run decides *what*
executes, in what *order*, and when it *stops*. Every statement here describes
behavior that already exists in the implementation (constitution §21–§24,
§41–§44, §54, §58–§59, §69) — it is a specification, not a roadmap.

If the code and this page ever disagree, one of them is a bug. The tests in
`tests/test_scheduler_semantics.py` pin the load-bearing claims so that a
refactor that changes them fails CI instead of silently drifting this document.

## 1. Vocabulary

| Term | Meaning |
| --- | --- |
| **Turn** | One `Runtime.arun()` / `arun_once()` / `astream()` call. Bounded by a `Budget` and `max_iterations`. |
| **Generation** (iteration) | One pass of the dispatch loop: drain events → match → schedule → run → commit. |
| **Run** | One agent execution inside a generation — the unit counted against `Budget.max_runs`. |
| **Event** | A derived record of a state change. Never authored by hand: committing an artifact create/update/delete *is* the event (`reactifact.events`). |
| **Commit** | One agent's compiled `Patch` applied to the `Context`, plus the reads it recorded. |

The runtime's job is to turn "what artifacts exist" into "what runs next". There
is no pre-declared execution graph; the schedule is a function of state (§63).

## 2. One generation, step by step

Implemented by `Runtime._arun_once_impl` (`reactifact/runtime.py`).

1. **Drain.** `context.drain_events()` returns the queued events in FIFO
   (emission) order and clears the queue. If the queue is empty, the generation
   yields **0 runs** and the turn is over (quiescence, §3).

2. **Match.** For each event, each agent (in priority order) is asked
   `agent.matching_triggers(event, context)`. A trigger matches on:
   - the **event type** (`artifact_created` / `updated` / `deleted` / `stale`),
   - **exact artifact-type equality** (not `issubclass`),
   - an optional `condition(artifact)` and/or `context_condition(artifact, context)`
     (the latter is what `CorrelatedConsume`/`JoinConsume`/`AbsentConsume` use to
     correlate across types).

   A `Consume(wakes=False)` contributes no trigger — it feeds inputs only. If a
   trigger's condition needs the artifact and it no longer resolves (deleted
   earlier in the same generation, or in a prior generation), the trigger does
   **not** match (`Trigger.matches`).

3. **Debounce.** If *every* matching trigger for an agent on an event carries
   `debounce=True`, the agent is collapsed: it gets at most **one** entry per
   generation, and the **last** matching event in the drained batch wins. An
   agent with a mix of debounced and non-debounced matches for that event is
   treated as non-debounced. A collapsed run costs **one** against `max_runs`.
   A debounced produce must read `inputs` (collected fresh from `Context`), not
   `event` — "which one changed" is exactly what debouncing discards.

4. **Order.** Non-debounced work is collected in `(event order × agent priority)`
   order; debounced entries are appended afterwards, then the whole list is
   **stable-sorted by `Agent.priority`** (ascending). So `priority` is the outer
   ordering key, and ties keep insertion order.

5. **Schedule (optional).** If a `Scheduler` is configured,
   `scheduler(context, work)` runs over the candidate list. The built-in
   `uncertainty_policy` filters → ranks deterministically → optionally asks the
   LLM to order the close top pair → optionally keeps top‑k. Two invariants are
   baked in: an **HITL-resume pin** (a candidate that unblocks an answered
   `PendingQuestion` is forced first) and a **no-starvation fallback** (filtering
   never empties a non-empty candidate list).

6. **Budget slice.** Work is cut to the remaining `max_runs`. If none remain,
   the outcome becomes `budget_runs_exceeded` and the generation stops. Note:
   events were already drained in step 1 — work dropped by the budget is **not**
   re-queued.

7. **Dispatch.** Agents run against the generation's **snapshot** (step 5 of
   §4). With concurrency caps they run through a global `max_concurrency`
   semaphore plus per-agent `concurrency_limit` tiers (global acquired first, in
   a fixed order, to avoid deadlock). Each produce is given its own `Effects`
   slot via a contextvar, so parallel produces never see each other's effects.

8. **Collect.** Results are gathered in **work order**, regardless of the order
   in which tasks actually finished (`asyncio.gather` preserves input order).

9. **Validate.** Every `Create` in a result's patch is checked against that
   agent's `produces` types and the `also_creates` they declare. An undeclared
   type raises before anything is applied.

10. **Commit.** For each agent, its `Patch` is applied as one unit, operations in
    order:
    - `Create` with an id that already exists becomes a **refresh** (new version
      of the same entity), not a duplicate (§42);
    - `Update` whose data is unchanged is a **no-op** — no new version, no event
      (so no-op patches never cascade, §41);
    - bumping a version emits `ARTIFACT_STALE` for artifacts whose producing
      commit read the old version (`Context.update` → `_dependents_of`).

    The generation returns the number of runs (every executed agent counts, even
    one whose patch was empty or that errored under isolation).

## 3. Termination and quiescence

`Runtime._arun_impl` repeatedly runs generations. It stops when **any** of:

- a generation yields **0 runs** (no drained events, no agent matched, or all
  remaining work was cut) → `completed`;
- the wall-clock deadline is reached → `budget_time_exceeded`;
- `max_runs` is exhausted → `budget_runs_exceeded`;
- the iteration limit is hit → `iterations_exhausted`.

There is **no structural termination guarantee**. A cycle (agent A creates `B`,
agent B updates `A`) never quiesces on its own; only the budget stops it. This is
deliberate: the framework does not run a cycle detector, and a reactive program
that genuinely wants to keep reacting is allowed to. Budgets are the backstop.

Because termination is budget-driven, a reactive program should always set a
budget in production. `Budget(max_runs=…)` is the cheapest guard; `max_seconds`
bounds a slow one.

## 4. Ordering guarantees

- **Events** are processed in FIFO emission order.
- **Work and commit order** follows `priority` (ascending, stable) and, within a
  generation, is **independent of task completion order**.
- **`Context.list_artifacts`** preserves insertion (creation) order, so a
  caller's own stable sort on a coarse key (e.g. `updated_at`) breaks in creation
  order.
- **Consequence:** with deterministic produces and a fixed agent list, the
  sequence of commits is deterministic *even under parallel dispatch* — the
  interleaving of side effects is not, but the resulting state transitions are.

## 5. Concurrency guarantees

- **Snapshot isolation per generation.** All agents in a generation read the
  same pre-commit `Context`; none sees a sibling's writes from the same
  generation. A changed read only becomes visible in the next generation.
- **Serialized commit.** Patches are applied only after every worker in the
  generation has finished — a generation is a barrier.
- **Bounded parallelism.** `Runtime.max_concurrency` caps globally;
  `Agent.concurrency_limit` caps a single agent (useful to throttle LLM-bound
  produces separately from cheap I/O).
- **Isolated effect slots.** One `Effects` slot per produce (a contextvar pushed
  by the runtime) means concurrent produces cannot observe each other's pending
  effects.
- Side effects inside a produce (LLM calls, network, disk) do run concurrently;
  only their **state changes** are serialized.

## 6. Budget semantics

`Budget` (`reactifact/budget.py`) is per turn and reset by `_begin_turn`.

| Knob | Enforced where | On exhaustion |
| --- | --- | --- |
| `max_runs` | work slicing + the match loop, in `_arun_once_impl` | `budget_runs_exceeded` |
| `max_seconds` | `_budget_exhausted()`, between generations; also published to `resources.budget_deadline` for tool loops | `budget_time_exceeded` |
| `max_iterations` | the `_arun_impl` loop bound | `iterations_exhausted` |
| `max_tool_calls` | tool loops (`ToolUse`/`LLMAgent`), not the runtime core | loop stops, honest answer |

`arun(max_iterations=…)` is used only when the active budget does not set
`max_iterations`; an explicit `Budget.max_iterations` wins.

## 7. Failure semantics

- **Fail loud by default** (§69). An exception raised by an agent propagates out
  of `arun()`/`astream()` and aborts the turn. Before it does, every sibling
  already scheduled in that generation is allowed to finish (the runtime gathers
  with `return_exceptions=True` and only then re-raises the first exception) — no
  in-flight task is left running unawaited.
- **`isolate_errors=True`** opts into resilience: the failing agent contributes
  no patch, `on_agent_error(agent, event, exc)` fires (if set), the run
  continues, and the failure is counted in `RunStats.errors`. Isolation is an
  explicit product decision, not a default safety net.
- **Reentrancy.** A `Runtime` instance is single-turn: a second concurrent
  `arun`/`arun_once`/`astream` on the *same* instance raises `RuntimeError`
  rather than racing on shared turn state. Use one `Runtime` per concurrent
  request; they may share `Context.resources`.
- **Observability** is delegated to `reactifact.tracing.RunTracer`: spans are
  built per generation and the turn's `RunTrace` is delivered once at turn end
  (`on_turn_end`) — see [Observability](observability.md).

## 8. Determinism: what is and is not guaranteed

**Guaranteed (given the same starting state, agents, and inputs):**

- events are derived from committed state — the causal chain cannot drift from
  the state;
- a generation is a snapshot followed by an ordered, serialized commit;
- commit order is independent of completion order;
- unchanged updates and `create(id=existing)` produce no new version and no
  event;
- `Context.list_artifacts` order is insertion order.

**Not guaranteed (must be controlled by the application to get reproducibility):**

- LLM/provider output (`from_env()` calls, tool results);
- wall-clock time — `Artifact.created_at`, `Event.timestamp`, span timing;
- auto-generated ids (`uuid4`) and anything seeded from randomness;
- iteration order of `set`/`dict` values *inside a produce's own logic* (reactifact's
  own ordering is insertion-based, but user code is on its honor);
- the relative interleaving of concurrent side effects;
- a `Scheduler`'s LLM tie-break, when enabled (it reorders the top two
  candidates).

To make a run reproducible, drive the LLM through a recorded provider and treat
time/ids as injected — `reactifact.replay` (`ReplayLLM`) replays recorded
decisions deterministically; see [Replay](replay.md).

## 9. Explicit non-guarantees

- **Single process.** The runtime schedules within one event loop; there is no
  distributed execution and no cross-process exactly-once.
- **Crash durability at commit boundaries, not cross-process transactions.**
  With the default `session_save_policy="per_commit"`, a session can be resumed
  from the last commit after a crash; that is resilience, not a distributed
  transaction.
- **No structural termination** (§3) and **no fairness across generations** — the
  no-starvation guarantee holds *within* one scheduling call, not across a whole
  turn.
- **Budget cuts drop work.** Once events are drained, work not run this
  generation is not re-queued.

## 10. Map to code and constitution

| Concept | Code | Constitution |
| --- | --- | --- |
| Generation loop | `reactifact/runtime.py` `_arun_once_impl`, `_arun_impl` | §21, §23, §24 |
| Matching | `reactifact/triggers.py` `Trigger.matches`, `reactifact/agents.py` `matching_triggers` | §22 |
| Debounce / order / budget slice | `runtime.py` `_arun_once_impl` | §24, §58 |
| Scheduling policy | `reactifact/scheduler.py` `Scheduler` | §26 |
| Concurrency | `runtime.py` `_dispatch` | §42, §58 |
| Atomic commit / idempotency | `runtime.py` `_commit_patches_to_apply`, `_apply_patch`; `context.py` `create`/`update` | §41, §42, §43 |
| Staleness | `context.py` `update` → `_dependents_of`, `stale_artifacts` | §43, §44 |
| Budget / outcomes | `reactifact/budget.py` | §58 |
| Failure model | `runtime.py` `_execute`, `_enter_turn` | §59, §69 |
| Observability | `reactifact/tracing/` | §54 |
