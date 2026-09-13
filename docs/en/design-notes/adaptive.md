# Design note — adaptive scheduling (§26, §24)

**Status:** spike (implemented) · **Scope:** a hook on the runtime so a
reactory can *decide* among candidates; hard rules may prune, ranking only
orders, an LLM tie-breaks rarely.

## Why this fits the codebase

The runtime is reactive fan-out: `arun_once` builds the candidate `work`
(agent×event) list and runs every matcher (§24). "Which step reduces
uncertainty most" has no home today — the hook is a single call in
`arun_once` before `_dispatch` and before the budget cap (so ordering also
matters when the budget slices the list):

```python
if self.scheduler is not None and work:
    work = await self.scheduler(self.context, work)
```

The default is `None` → current behavior; primitives are untouched.

## The policy contract (three stages + two guards)

```text
filter (hard rules → MAY drop) → rank (metric → orders only) → LLM tie-break (rare)
```

- **filter** — domain rules prune candidates that don't fit at all, so they
  never reach ranking (e.g., a refuted hypothesis, or capability 'b' not
  allowed for tag 'x').
- **rank** — the metric orders candidates; it never drops (§26: not every
  decision needs an LLM). Optional `rank_limit=k` trims to the top-k *after*
  ranking (pinned HITL candidates are never counted; a non-empty ranked list
  is never emptied → no starvation).
- **LLM tie-break** — only when the top-two metric gap is ≤ `llm_tie_break`
  *and* a model exists; one `structured_llm` call orders the pair. Offline →
  skipped.
- **HITL pin** — any candidate that resolves an *answered* `PendingQuestion`
  is forced to the front (§60): a human approval/mutant can never lose to
  ranking.
- **No-starvation fallback** — if filtering would empty the candidate set, the
  original list is kept: the only path to progress must survive.

Adaptivity emerges effort-restricted: each iteration re-ranks against the fresh
context, so the highest-value candidate's events drive the next round (§24).

## API

```python
from reactifact import Runtime
from reactifact.scheduler import uncertainty_policy

runtime = Runtime(
    ctx,
    agents=[...],
    scheduler=uncertainty_policy(
        rules=[not_refuted],        # Rule = (context, agent, event) -> bool
        metric=support_split,       # Metric = (context, agent, event) -> float
        llm_tie_break=0.05,         # LLM on near-tie (rare), app-owned `llm_system=`
        rank_limit=1,               # optional "choose top-k" (safe: never empties)
    ),
)
```

`Agent.capabilities` (§25) is metadata the policy and the LLM tie-break use to
describe candidates; it changes nothing on its own.

`reactifact.scheduler.relation_balance_metric` is the built-in
uncertainty-driven `Metric`: ranks a candidate by how lopsided the
`supports`/`contradicts` relations are on the artifact its event is about —
deterministic, provider-agnostic, the same structural signal
`examples/medic_lab` used to compute by hand for its report only
(`produce/evaluate.py`). `medic_lab` now wires it into its own
`Runtime(scheduler=medic_lab_scheduler())`, so the most-contradicted open
hypothesis is actually investigated first, not just ranked highest
afterward.

## What it is NOT

- Not an alternative executor — no path graph; the runtime still reacts to
  events.
- Not "must pick one" — ranking only; dropping is the *filter's* job, guarded
  by the no-starvation fallback.
- Not an LLM everywhere — ties only, budgeted, offline-safe.
- The tie-break system prompt is app-owned: pass `llm_system=` to
  `uncertainty_policy`/`Scheduler` (default `DEFAULT_TIE_BREAK_SYSTEM`), so the
  decision can be phrased in the domain's terms.

## Shipped with the spike

- `reactifact/scheduler.py` — `Scheduler`, `uncertainty_policy`,
  `relation_balance_metric`, types.
- Runtime hook (`scheduler=` param), `Agent.capabilities`.
- `examples/adaptive` — two competing artists + HITL approval.
- `examples/medic_lab` — `medic_lab_scheduler()` wires `relation_balance_metric`
  into both the console (`chat.py`) and web (`api/chat.py`) entrypoints.
- `tests/test_adaptive.py` — ranking, pruning, fallback, LLM tie-break,
  offline skip, HITL pin, demo runs, `relation_balance_metric` (weights,
  custom relation names, end-to-end scheduling).
- `tests/test_medic_lab.py` — `medic_lab_scheduler()` prioritizes the
  more-contradicted open hypothesis.