"""StepCall and the per-run contextvars."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from ..artifacts import Artifact
from ..context import Context
from ..effects import Effects
from ..events import Event

_ACTIVE_CONTEXT: ContextVar[Context | None] = ContextVar(
    "reactifact_step_context", default=None
)
_ACTIVE_CALL: ContextVar[StepCall | None] = ContextVar(
    "reactifact_step_call", default=None
)


def current_step_context() -> Context | None:
    """The `Context` of the step currently running, or `None` outside a run."""
    return _ACTIVE_CONTEXT.get()


def set_step_context(context: Context | None) -> Token[Any]:
    return _ACTIVE_CONTEXT.set(context)


def reset_step_context(token: Token[Any]) -> None:
    _ACTIVE_CONTEXT.reset(token)


def current_step_call() -> StepCall | None:
    """The `StepCall` of the step currently running, or `None`."""
    return _ACTIVE_CALL.get()


def set_step_call(call: StepCall | None) -> Token[Any]:
    return _ACTIVE_CALL.set(call)


def reset_step_call(token: Token[Any]) -> None:
    _ACTIVE_CALL.reset(token)


@dataclass(frozen=True, slots=True)
class StepCall:
    """The single argument `step()` receives, both class-style and
    `@step`-decorated functions.

    - `context` — the working `Context` (queries, `list_artifacts`, ...).
    - `inputs` — this agent's whole declared working set (via `consumes`),
      identical for every produce this agent runs in this call — not
      filtered per produce, and not specific to whichever event triggered
      this particular run (see `trigger` for that).
    - `event` — the raw event, if any (`None` for a scheduler-driven run
      with no specific triggering event, e.g. the adaptive `Scheduler`).
      Still the only way to know *what* was deleted on a DELETED event,
      since `trigger` necessarily can't be (the artifact's data is gone).
    - `trigger` — the already-resolved artifact behind `event`
      (`context.get(event.artifact_id)`), or `None`. Guaranteed non-`None`
      whenever this produce also declares `reacts_to` and `event.type` is
      CREATED/UPDATED/STALE — see `Step`'s own docstring for the full
      contract (and its one exception, a DELETED event on the produce's own
      `reacts_to` type, where `None` here *is* the event, not a gap).
      Without `reacts_to`, still resolved and passed whenever `event` isn't
      `None`, but purely as a convenience — there's no per-type contract to
      enforce, so it's never a reason `Agent.execute()` skips the call.
    - `effects` — the produce-scoped `Effects` authoring surface (the same
      contextvar-backed slot as a class-style `Step`'s `effects` parameter;
      exposed here too since a `@step`-decorated function has no `self`
      to hang it from).
    """

    context: Context
    inputs: list[Artifact[Any]]
    event: Event | None = None
    trigger: Artifact[Any] | None = None
    #: The step's correlation value for this run (e.g. its `query_id`).
    scope: Any = None
    #: Per-artifact input cursor: type -> {artifact id -> version seen}.
    #: This is the step's watermark (what it has already consumed).
    cursor: dict[type, dict[str, int]] = field(default_factory=dict)

    @property
    def effects(self) -> Effects:
        """See `Step.effects` — same contextvar slot, same guard."""
        from ..effects import current_effects

        slot = current_effects()
        if slot is None:
            raise RuntimeError(
                "StepCall.effects is only available while the runtime "
                "executes this produce — write effects inside step(), "
                "not before it."
            )
        return slot

    @property
    def request(self) -> Mapping[str, Any]:
        """The active turn's request mapping (`Runtime.arun(request=...)`).

        Empty when no request was passed. Read-only; use it for per-request
        data (an authenticated user, a tenant id, a correlation id) instead of
        a hand-rolled `ContextVar` or a `resources`-side dict (§52).
        """
        from ..request import current_request

        return current_request()
