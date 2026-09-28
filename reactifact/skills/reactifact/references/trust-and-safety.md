# Trust & safety — guardrails, authorization, quota

Three opt-in layers, all configured on `RuntimeResources`. None change behavior
by default. They run at **artifact boundaries and resource access** (reactifact's
typed state), not around model messages — so they are deterministic and testable.

## Guardrails — `reactifact.guardrails`

Validate/rewrite an artifact's data *before the runtime commits it*. Every
`Create`/`Update` an agent produces is checked; a violation blocks the turn or
redacts the data.

```python
# not-run: illustrative
from reactifact import RuntimeResources
from reactifact.guardrails import GuardrailPolicy, InjectionGuardrail, PIIGuardrail
from reactifact.redaction import RegexRedactor

resources = RuntimeResources(
    guardrails=GuardrailPolicy(
        [PIIGuardrail(redactor=RegexRedactor()), InjectionGuardrail()],
        on_violation="block",  # or "flag"
    ),
)
```

- A guardrail is any object with `name` and
  `check(data, context) -> GuardrailDecision`.
- `GuardrailDecision.action`: `allow`, `redact` (with `data=`), `violation`
  (defer to the policy), or `block`/`flag` (override the policy).
- Built-ins: `PIIGuardrail` (any `Redactor`), `InjectionGuardrail`,
  `DenyListGuardrail`, `SizeGuardrail`, and the generic `PatternGuardrail`.
- A `block` raises `GuardrailViolation` out of `arun()` (or drops that
  generation with `isolate_errors=True`); `flag` records a metric
  (`reactifact_guardrail_triggered_total`) and lets it through.
- Guardrails run on **agent-produced** artifacts. To screen a seeded input,
  call `policy.evaluate(data, context)` yourself before `context.create(data)`.

## Authorization — `reactifact.authz`

A `Principal` (who the run acts for) plus an `Authorizer` policy gating the
points that matter: running an agent, and creating/updating/deleting artifact
types (plus tool execution — see below).

```python
# not-run: illustrative
from reactifact import RuntimeResources
from reactifact.authz import PermissionPolicy, Principal

resources = RuntimeResources(
    principal=Principal(id="alice", capabilities=("analyst",)),
    authorizer=PermissionPolicy(
        {"analyst": {"run:*", "create:Answer"}},
        default_allow=False,  # anonymous principals are denied
    ),
)
```

- `PermissionPolicy.grants` maps a capability to `"action:resource"` patterns
  (`"run:*"`, `"create:*"`, `"*"`); a principal is allowed when any of its
  capabilities matches.
- Enforced by the runtime on `run` (agent name) and `create`/`update`/`delete`
  (artifact type), and by the tool loop on `execute` (tool name). Produces can
  gate their own collaborators with `resources.require_authorized("read", id)`.
- Denial raises `AuthorizationError` (a `PermissionError`) from `arun()`.

## Quota — `reactifact.quota`

A `Budget` bounds one turn; a **quota** bounds a principal across turns: total
tokens, cost or calls within a rolling window.

```python
# not-run: illustrative
from reactifact import RuntimeResources
from reactifact.authz import Principal
from reactifact.quota import Quota, QuotaTracker

resources = RuntimeResources(
    principal=Principal(id="acme"),
    quota=QuotaTracker(Quota(max_tokens=1_000_000, window_seconds=86_400)),
)
```

- The runtime checks the principal's quota at the start of each turn; an
  exhausted key sets `RunOutcome.QUOTA_EXCEEDED` and runs nothing. Usage is
  counted for the turn via `QuotaLLM` (cache hits are not charged).
- The default store is in-process — like LangChain's `InMemoryRateLimiter`, it
  does not coordinate across workers. Pass `store=` (the tiny `QuotaStore`
  protocol) backed by shared state for a cluster.

## Composing

All three live on one `RuntimeResources` and stack with `Budget`, `Metrics`,
`Redactor`, `Verify` and the approval gate. `MetricsTracer` records guardrail
triggers and quota-exceeded runs alongside everything else, so “why was this
turn blocked” is answerable from the same observability data.
