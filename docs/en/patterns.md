# Patterns

Reusable patterns observed across the examples. They are not abstract —
each is concretely instantiated in `examples/`.

## TL;DR

| Do | Don't | Why |
| --- | --- | --- |
| Write `self.effects.create/update/link/ask(...)`, return `None` | Assemble a `Patch` by hand in an ordinary produce | The runtime compiles one atomic patch per produce — building it yourself is the escape hatch, not the default |
| Guard eligibility with an early `return None` | Rely on scheduling order to skip work that isn't ready | Eligibility is a state decision, not a lucky accident (§69) |
| Use stable ids (`answer:{qid}`) or `effects.create_once(...)` | Re-derive an id from a counter or timestamp | Idempotent re-runs — the same event twice must not duplicate state |
| Return `None` on missing model / failed parse, then show an honest fallback | Substitute a canned "confident-sounding" answer when the LLM call fails | Deterministic work stays deterministic; generative failure must be visible, not papered over |
| Keep `next_status`/scoring functions pure `(context, key) -> ...` | Mix LLM calls or side effects into a `StatusMachine.next_status` | Pure functions are unit-testable without a runtime |
| Use `Runtime(isolate_errors=True, on_agent_error=...)` only when you've decided partial progress is acceptable | Reach for `isolate_errors` as a default to silence exceptions | Default is fail-loud (§69) — isolating errors is an explicit product decision, not a safety net |

Each row links to the fuller pattern below.

## HITL: humans as first-class participants

A human is just another reaction to the context. The runtime represents a
question with `PendingQuestion`:

```python
class PendingQuestion(BaseModel):
    question: str
    kind: str = "general"          # e.g. "clarify", "approval"
    notes: dict[str, Any] = {}     # routing info ("which agent asked")
```

A produce creates one with `self.effects.ask(...)`, and the human's answer
comes back through `self.effects.resume(...)` (an effect that marks the
`PendingQuestion` answered, §60):

```python
self.effects.ask("Approve the estimate?", kind="approval")
# ... the web/UI sees a pending question and shows a waiting state ...
self.effects.resume(question_artifact, "да")
return None
```

The producing agent sees the answer as a new event (the corresponding
`PendingQuestion` artifact is updated). Web demos query
`context.pending_questions()` to know whether to render a "waiting" state.

Pattern: **activate stage → immediately ask** (the repair `ApprovalStage`
creates the approval `PendingQuestion` the moment it becomes eligible, without
waiting for a user message), then **react to the answer** on the next event.

For a re-derivable question (`f"steer:{qid}:{round}"`, `medic-lab`'s steering
ask), pass `id=` to `effects.ask(...)` so a guard
(`if context.get(id) is not None: return None`) can stop the produce from
asking again while the question is still unanswered — the same idempotency
idiom as `effects.create_once`.

## Tool agents: LLM + tools (blocking or HITL)

For "the model decides which tool to call" flows, use the built-in agents:

```python
from reactifact import Consume, Produce
from reactifact.llm_agent import HITLLMAgent

class OpsAgent(HITLLMAgent):
    name = "ops"
    system = "You run Kubernetes/GitLab/Ansible tasks."
    tools = [...]        # FunctionTool instances
    max_steps = 8
    max_asks = 2
    consumes = [Consume(Project)]
    produces = [Produce(Report)]
```

- `LLMAgent` — blocking loop: the LLM emits `tool_call`, the runtime runs the
  tool, the observation feeds the next step. No human in the loop.
- `HITLLMAgent` — same, plus the LLM can emit `ask`: a `PendingQuestion` is
  created, the loop pauses, and the human's answer returns as
  `Observation(source="user")`. Tool *execution* itself is also gated by
  `ToolUseHITL`, so risky commands wait for a human click before running.

The `devops` example is the canonical `HITLLMAgent` demo (LLM tool router +
approval for K8s/GitLab/Ansible mutations).

## Structured output: never parse raw JSON yourself

The runtime wraps a single LLM call into a `pydantic` schema with retries and
lenient JSON parsing:

```python
from reactifact.structured import StructuredLLM, structured_llm

# procedural variant:
body = await structured_llm(
    context, schema=AnswerBody,
    system="You assemble coherent answers.",
    user=f"Question: {question}\nFacts: {facts}",
)

# re-usable object variant:
_extractor = StructuredLLM(ProjectInfo, system="Extract repair facts; unknown = null")
facts = await _extractor.call(context, user=message_text)
```

Both return `None` on a missing model or a parse failure after retries — and the
caller is expected to handle `None` (see fallbacks). If you need to tell
"no provider configured" apart from "the provider is down" (e.g. to alert on
a real outage) without changing that `None` handling, pass `on_error`:

```python
def alert_if_down(reason: str, exc: Exception | None) -> None:
    if reason == "provider_error":
        logger.error("LLM outage: %r", exc)

body = await structured_llm(context, schema=AnswerBody, user=text, on_error=alert_if_down)
```

Build the `system`/`user` strings with `PromptTemplate` (core): declared
variables, a `KeyError` on missing vars, and model-attribute fields
(`template.render(topic=…, question=…)`) — no hand-rolled `.format` in app code.

`StructuredGenerateAgent` is the declarative wrapper: override
`build_prompt(inputs)`, optionally `fallback(inputs)`, declare `schema` — and
the reading/writing provenance is recorded for you.

## Plan-and-execute: draft once, run one step at a time

For a goal that decomposes into an ordered sequence of dependent steps
(rather than independent chunks — see map-reduce above), split planning from
execution into two produces instead of one big tool loop:

```python
class Planner(Produce[PlanStep]):
    artifact_type = PlanStep

    async def produce(self, context, inputs, event=None):
        goal = context.get(event.artifact_id) if event is not None else None
        if goal is None or context.list_artifacts(PlanStep):
            return None  # not a Goal, or already planned (§42)
        steps = await plan_steps(goal.data.text)  # structured LLM, or a fallback
        for index, instruction in enumerate(steps):
            self.effects.create(PlanStep(index=index, instruction=instruction), ...)


class Executor(Produce[StepResult]):
    artifact_type = StepResult

    async def produce(self, context, inputs, event=None):
        steps = sorted(context.list_artifacts(PlanStep), key=lambda s: s.data.index)
        for step in steps:
            if context.get(f"result:{step.id}") is not None:
                continue  # already executed
            if step.data.index > 0 and context.get(f"result:{steps[step.data.index-1].id}") is None:
                return None  # wait for the predecessor's result (§69)
            self.effects.create(StepResult(...), id=f"result:{step.id}")
            return None  # one step per generation; the next result re-triggers us
```

The key move: the executor does **not** branch on `event.artifact_id` the way
a per-chunk map-reduce produce does — it recomputes "which step is next" from
state on every trigger (consuming both `PlanStep` and `StepResult`), the same
"eligibility is a state decision" idiom `Combine` uses in `map_reduce`. That
`if step.data.index > 0 and ... is None: return None` guard is the entire
sequencing mechanism — no explicit control-flow graph, no manual "wait for
node N" wiring. A `Finisher` produce mirrors `map_reduce`'s `Combine`: wait
until every step has a result, then synthesize the final answer.

See `examples/plan_execute` for the full port (structured planning with a
deterministic single-step fallback, and the finisher).

## Fallbacks: honest degradation

Deterministic work stays deterministic; generative work degrades *honestly*:

1. If **no model is configured** — use the deterministic variant
   (canned options, fallback plans): demo mode without a key.
2. If a **model returns nothing usable** — do NOT substitute canned answers;
   report the failure openly: *"Не удалось подобрать варианты…"*.

The `repair` example implements both paths in `_make_design_options`:
`fallback_options` only when `context.resources.llm is None`, otherwise a
clear failure message.

## Cost/rollback model ("change → rebuild")

Long multi-stage conversations occasionally need to *go back*. The `repair`
example models this as: parse the change request → determine the earliest stage
affected → reset everything downstream deterministically:

```python
target = rollback_target(changed)      # "plan" | "estimate" | …
updates = _downstream_resets(target)   # clears design_options/plan/estimate
updates |= {"stage": target, "info": new_info, "handled_msg": ""}
```

Resetting `handled_msg` re-arms the stages so the rebuild actually runs. This
is the manual twin of `StatusMachine` — for those workflows where rollback is
part of the product, not a lifecycle.

## Budget and fairness

`Budget` caps a run:

```python
runtime = Runtime(ctx, agents=[...], budget=Budget(max_runs=200), max_concurrency=2)
```

- `max_runs`, `max_iterations`, `max_time_s`, tool-call caps — the runtime
  stops and reports `RunOutcome` (`completed` | `budget_exhausted` | …) with
  `RunStats`.
- `Agent.concurrency_limit` (LLM-bound agents default to a lower cap) + the
  runtime's global `max_concurrency` keep provider rate limits happy — the
  `medic-lab` demo runs a hypothesis laboratory with a LLM-limit of 2 inside a
  global cap of 6.
- By default one agent's exception propagates out of `arun()`/`astream()` and
  stops the whole run (§69 — fail loud, not silently). Opt into isolating it
  instead with `Runtime(isolate_errors=True, on_agent_error=...)`: that
  agent contributes no patch this generation, unrelated agents still make
  progress, and the failure is traced (`AgentSpan.error`) and counted
  (`RunStats.errors`) rather than hidden.

## Chat memory with sessions

State lives in the context, so *chat memory is just state*. Across requests:

```python
store = SessionStore(FileKVBackend("sessions"))
session = await store.open(session_id, resources=resources)
# ...create UserMsg, astream, await session.save()
```

`store.open` rehydrates the context from the last checkpoint; a background
agent (`@consume`/trigger) can trim history, update a `handled_msg` pointer, and
patch pending questions. The web demos ship this pattern verbatim.

## Status machines for long lifecycles

See [recipes](recipes.md). The rule of thumb for choosing between patterns:

| Situation | Approach |
| --- | --- |
| An artifact moves through phase states | `StatusMachine` + verify-produce |
| A workflow needs to roll *back* on user edits | stage guard + `_downstream_resets` |
| Explore & compare alternative states | `branch()` + `merge()` (§39-§40) |
| "Which of these did the model pick?" | `PickStage`-style parse + guard |

## Determinism as a habit

- The **produce contract**: write `self.effects.create/update/link/ask(...)` and
  return `None`; the runtime compiles the slot into one atomic patch (§24).
  `Patch` is the transport — you rarely type it in an ordinary produce.
- Make eligibility a guard, not a lucky scheduling accident (`return None` early).
- Prefer stable ids (`answer:{qid}`, `ref:{sid}:{owner}`) → idempotent re-runs.
  `self.effects.create_once(Model(...), id=...)` folds the "already done"
  guard into the call — `None` back means skip, don't rebuild the guard by
  hand above every `create`.
- Pure decision functions (`next_status`) are unit-testable without a runtime.
- Every LLM call has a structured schema, a retry budget, and a `None` path.