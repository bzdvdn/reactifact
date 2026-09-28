# Trust & safety

Three opt-in, library-level layers for running reactifact with untrusted input
and multiple principals. They act on **typed artifacts and resource access**, so
they are deterministic and testable — unlike middleware wrapped around model
messages. Everything is configured on `RuntimeResources` and is off by default.

## Guardrails

A guardrail validates or rewrites an artifact's data *before the runtime commits
it*. Every `Create`/`Update` an agent produces is checked:

```python
from reactifact import RuntimeResources
from reactifact.guardrails import GuardrailPolicy, InjectionGuardrail, PIIGuardrail
from reactifact.redaction import RegexRedactor

resources = RuntimeResources(
    guardrails=GuardrailPolicy(
        [PIIGuardrail(redactor=RegexRedactor()), InjectionGuardrail()],
        on_violation="block",          # or "flag"
    ),
)
```

A guardrail is any object with `name` and `check(data, context) ->
GuardrailDecision`. The decision is `allow`, `redact` (with the replacement
`data`), `violation` (defer to the policy's `on_violation`), or `block`/`flag`
(override the policy for that guardrail). Built-ins:

| Guardrail | Checks |
| --- | --- |
| `PIIGuardrail(redactor=…)` | redacts PII in string fields via any `Redactor` |
| `InjectionGuardrail()` | a conservative prompt-injection lexicon |
| `DenyListGuardrail(patterns, …)` | caller-supplied deny list |
| `SizeGuardrail(max_chars, field=…)` | an input length cap |
| `PatternGuardrail(patterns, …)` | the generic regex form |

`block` raises `GuardrailViolation` out of `arun()` (or drops that generation
under `isolate_errors=True`); `flag` records
`reactifact_guardrail_triggered_total` and lets the artifact through. Redaction
runs in order, so a `PIIGuardrail` can scrub text before an `InjectionGuardrail`
sees it. Guardrails apply to agent-produced artifacts; call
`policy.evaluate(data, context)` yourself to screen a seeded input.

## Authorization

A `Principal` (the user/tenant/service a run acts for) plus a policy gating what
it may do:

```python
from reactifact.authz import PermissionPolicy, Principal

resources = RuntimeResources(
    principal=Principal(id="alice", capabilities=("analyst",)),
    authorizer=PermissionPolicy(
        {"analyst": {"run:*", "create:Answer"}},
        default_allow=False,           # anonymous principals are denied
    ),
)
```

`PermissionPolicy.grants` maps a capability to `"action:resource"` patterns
(`"run:*"`, `"create:*"`, `"*"`). The runtime enforces `run` (agent name) and
`create`/`update`/`delete` (artifact type); the tool loop enforces `execute`
(tool name); produces can gate their own collaborators with
`resources.require_authorized("read", id)`. Denial raises `AuthorizationError`.

## Quota

A `Budget` bounds a single turn; a quota bounds a principal across turns:

```python
from reactifact.quota import Quota, QuotaTracker

resources = RuntimeResources(
    principal=Principal(id="acme"),
    quota=QuotaTracker(Quota(max_tokens=1_000_000, window_seconds=86_400)),
)
```

The runtime checks the quota at the start of each turn; an exhausted key sets
`RunOutcome.QUOTA_EXCEEDED` and runs nothing. Usage is counted through the turn
via `QuotaLLM` (cache hits are not charged). The default store is in-process —
like LangChain's `InMemoryRateLimiter`, it does not coordinate across workers;
pass `store=` (the tiny `QuotaStore` protocol) backed by shared state for a
cluster.

## Relationship to the other primitives

`Redactor` (trace redaction) and the destructive-tool approval gate predate this
layer and still work on their own; trust & safety composes them into a policy
surface and adds authorization and quota. `MetricsTracer` records guardrail
triggers and quota-exceeded outcomes, so “why was this turn blocked” is
answerable from the same traces and metrics as everything else.
