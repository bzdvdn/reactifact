# plan_execute

**Plan-and-Execute** (LangChain/AutoGPT-style): a planner drafts an ordered
list of steps up front, an executor runs them **one at a time**, feeding each
step's output into the next step's prompt, and a finisher synthesizes the
answer once every step has a result.

```bash
uv run python -m examples.plan_execute.main
```

Contrast with `map_reduce`: there, chunks are independent and the runtime
fans them out in parallel. Here, steps are sequentially dependent — the
executor produce declares itself ineligible (returns without creating
anything) until the previous step's `StepResult` exists. No graph is drawn by
hand; the ordering falls out of what artifacts exist yet (§69).

Demonstrates: sequential multi-step execution as a state precondition rather
than an explicit control-flow graph, structured-output planning, deterministic
offline fallback.

Layout (same split as `ledger`/`devops`): `models.py` (artifacts), `prompts.py`
(templates + the offline fallback), `produce.py` (`Planner`/`Executor`/
`Finisher`), `agents.py` (the single `Flow` agent), `main.py` (CLI entrypoint).

## Recipe version

`main_recipe.py` runs the identical scenario built on
[`reactifact.recipes.PlanExecute`](../../docs/en/recipes.md#planexecute)
instead of hand-rolled produces — compare it to `produce.py`+`agents.py` to
see exactly what the recipe takes off your hands (ordering, gating, idempotent
re-entry, completion detection) versus what stays yours (the three prompts).

```bash
uv run python -m examples.plan_execute.main_recipe
```
