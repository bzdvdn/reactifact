# supervisor

**Port of the router + specialist + human approval** pattern (CrewAI roles,
AutoGen two-agent approval, LangGraph HITL gate).

A `Request` is routed (structured LLM `RouteBody`, deterministic keyword
fallback) to a specialist produce; the specialist returns a `SpecialistReport`;
then a supervisor produce asks the human to **approve** (`effects.ask`) and
records the answer with `effects.resume` (§60). "да" → the report is the final
reply; otherwise an honest "please refine" reply.

```bash
uv run python -m examples.supervisor.main
```

Run(`run()`) simulates the human answer so the CLI and tests complete
deterministically; a real web/chat app would stream the pending question and
let the user answer.

Demonstrates: `effects.ask`/`effects.resume` HITL, role-based produces, routing
as structured output.

## Recipe version

`main_recipe.py` runs the identical scenario built on
[`reactifact.recipes.Router`/`ApprovalGate`](../../docs/en/recipes.md#router--approvalgate)
instead of hand-rolled `RouteTask`/`Supervisor` produces — the routing
fallback and the approval-question bookkeeping (tracking a thread's *whole*
question history, not just the unanswered ones) move into the recipe;
`Specialist` (the actual routed work) stays exactly as hand-written, since
there's nothing generic about it.

```bash
uv run python -m examples.supervisor.main_recipe
```
