# Troubleshooting

This page is not a feature list of the tracing/observability tooling — that's
[observability.md](observability.md). This is the other direction: you have a
specific "why did/didn't this happen" question and want the fastest path to
an answer, organized by symptom.

The common thread: in reactifact, *eligibility is a state decision* (§69) —
nothing calls an agent directly, so there is no stack trace pointing at "the
line that should have called it." The tools below all answer the same
underlying question a different way: **what did the state actually contain
when the runtime decided what to run?**

## "My agent never ran"

Eligibility is decided by `Consume`/`Trigger` matching against events, not by
code order — so "it never ran" almost always means either the event that
should have triggered it never fired, or its `Consume.by_field`/`by_status`
condition was false for the artifact that exists.

- `reactifact graph <module>` (e.g. `reactifact graph examples.knowledge.agents`)
  prints the *static* blueprint — every agent's declared `consumes`/`produces`.
  Confirm the agent is actually subscribed to the artifact type you expect;
  a typo'd or stale `Consume(WrongType)` is the most common cause.
- A frequent "silent no-op" that looks like a missed trigger but isn't:
  `effects.create_once(..., id=...)`/`upsert(...)` intentionally does nothing
  when that id already exists (§42, idempotency) — check whether the artifact
  you expected to be freshly created already existed from an earlier
  generation.
- `agent.matches(event, context)` is the exact boolean the runtime calls —
  callable directly in a REPL against a real `Event` if the graph looks right
  but you're still unsure.

## "My agent ran, but produced nothing"

A produce returning early (`return None`) is the normal, honest shape of "not
eligible yet" or "nothing to do" (§59) — it is not itself an error, so it
won't show up as one.

- `reactifact trace <path/to/traces.db> [run_id]` renders the run as a Mermaid
  diagram (`trace_to_mermaid`, defaults to the latest run) — each agent's span
  shows its reads/writes, and any raised exception is inlined right on the
  node (`⚠ <message>`), so this is the fastest way to see *which* agent
  actually erred versus *which* legitimately declined to act.
- If nothing errored and the span shows no writes, the guard inside your
  `produce()` returned `None` — this is a case for a breakpoint/print at the
  specific `return None` sites, not for the tracer (the tracer records what
  happened, not why a guard chose not to act).
- `Runtime(isolate_errors=True, on_agent_error=...)` is opt-in — by default
  reactifact fails loud and an exception propagates out of `arun()`/`astream()`
  (§69). If you deliberately turned isolation on, check
  `runtime.last_stats.errors` (`RunStats.errors`, §58) for the count, and the
  trace's `⚠` markers for which agent(s).

## "My agent ran more times than expected"

- Missing idempotency: `effects.create(...)` with no stable `id=` (or a
  derived-from-a-counter one) creates a new artifact on every matching event;
  `effects.create_once(..., id=...)` is the guard that should usually be
  there instead (§42).
- Self-triggering: an agent that both `consumes` and `produces` the same
  artifact type re-fires itself on its own output unless a guard
  (`if <already-done-condition>: return None`) stops it — see the
  `already planned (§42)`/`already executed` guards in `examples/plan_execute`
  for the pattern.
- `reactifact trace` again: repeated writes to the same artifact id across
  generations in the diagram is the direct symptom.

## "The run stopped earlier than I expected"

- Check `runtime.outcome` (`RunOutcome`, §58) after `arun()`/`astream()`
  (also on `runtime.last_stats.outcome`):
  `budget_runs_exceeded`/`budget_time_exceeded`/`iterations_exhausted` mean a
  `Budget` limit was hit, not a bug — `completed` means the run genuinely
  settled (a generation produced nothing further to react to).
- If the outcome is `completed` sooner than expected, that's usually the
  actual reactive model working as designed: nothing further became eligible.
  Go back to "My agent never ran" above for *that* agent specifically.

## "The scheduler picked/dropped an agent I didn't expect" (if you use `scheduler=`)

Only relevant if you passed `Runtime(scheduler=...)` — without one, every
eligible candidate runs, in declared-`priority` order.

- Filter rules can drop a candidate entirely before it's ever ranked; ranking
  only reorders, never drops; `rank_limit` trims the ranked list *after*
  ranking. The no-starvation fallback means a candidate only truly
  disappears via a `rules` filter or `rank_limit` — never as a "the ranking
  metric scored it too low to run at all" surprise, because ranking alone
  never drops.
- See [design-notes/adaptive.md](design-notes/adaptive.md) for the full
  filter → rank → LLM-tie-break contract, and
  `reactifact.scheduler.relation_balance_metric` if you're using the built-in
  uncertainty-driven metric (§26) — it reads `supports`/`contradicts`
  relations, so an unexpected order often means those relations aren't
  linked the way you think (`context.incoming(artifact_id, relation=...)`
  to check directly).

## "I don't understand why the answer says what it says"

This isn't a failure case — it's what provenance is for (§15, §34):

- `context.related(answer.id, "supported_by")` walks one hop of outgoing
  links from an artifact (skips dangling ones); `context.incoming(id,
  relation=...)` walks the reverse direction.
- `context_to_mermaid()` / `trace_provenance_to_mermaid()` (`reactifact.viz`)
  render the whole link graph or a trace's provenance chain as a diagram —
  see [comparison.md §3](comparison.md#3-provenance-bolt-on-vs-built-in) for
  the worked example.

## Reproducing a problem deterministically

Once you know *what* happened, `reactifact.testing.ScenarioLab` lets you pin
it down without depending on a live LLM or a live run: seed the exact
artifacts, run to completion, and assert on the result
(`.artifacts(...)`/`.tools`/`.path`/`.errors`); `lab.fail(tool_name, error)`
and `lab.fail_resource(...)` inject the specific failure you're chasing; a
`ReplayLLM`-recorded model response makes a flaky provider's output
deterministic across reruns. There's no dedicated guide yet — start from the
`reactifact.testing` module docstring and the worked examples in
`examples/repair/scenarios/` and `examples/knowledge/scenarios/`.
