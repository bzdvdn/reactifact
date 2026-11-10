from __future__ import annotations

import time
from abc import ABC
from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from .artifacts import Artifact
from .consume import Consume
from .context import Context
from .effects import current_effects
from .events import Event, EventType
from .patches import Patch
from .step import (
    Step,
    StepCall,
    _scope_matches,
    _scope_value,
    invoke_step,
    reset_step_call,
    reset_step_context,
    set_step_call,
    set_step_context,
)
from .tools import reset_current_produce, set_current_produce
from .triggers import Trigger

#: Per-task log of the produces that ran for the current agent execution:
#: ``(produce_class_name, operations_authored, latency_ms, started_at)``. ``Runtime._execute``
#: opens it (and later records the span), ``Agent.execute`` appends — a
#: contextvar so concurrent executions sharing one ``Agent`` instance never race.
_produce_runs: ContextVar[list[tuple[str, int, float, datetime]] | None] = ContextVar(
    "reactifact_produce_runs", default=None
)


def begin_produce_runs() -> Token[Any]:
    """Opens the per-task produce-run log (see ``_produce_runs``)."""
    return _produce_runs.set([])


def produce_runs() -> list[tuple[str, int, float, datetime]]:
    """The produce runs recorded for the current task (empty if none)."""
    return _produce_runs.get() or []


def end_produce_runs(token: Token[Any]) -> None:
    _produce_runs.reset(token)


class Agent(ABC):  # noqa: B024 — interface without abstract methods, run() has a default
    """Base container: consumes some artifacts and produces others.

    If `run` is not overridden, automatically collects inputs according to
    consumes and calls produce on every produce, merging the patches.
    """

    name: str = ""
    consumes: Sequence[Consume] | None = None
    #: The steps this agent runs. Canonical spelling; `produces` is a
    #: deprecated alias kept for 0.1.x code.
    steps: Sequence[Step[Any]] | None = None
    #: Deprecated alias for `steps` (class-level and instance-level).
    produces: Sequence[Step[Any]] | None = None
    #: Declarative capability labels (§25), consumed by the adaptive policy.
    capabilities: tuple[str, ...] = ()
    #: Explicit trigger override — for the *imperative* style (`Agent`
    #: subclass overriding `run()` directly, no `consumes`/`steps` at all,
    #: see `run()`'s own docstring). That style never calls
    #: `_collect_inputs()`, so `Consume` has nothing to attach to; `triggers`
    #: is the only way such an agent says when to wake up. For the
    #: declarative style (`consumes`/`steps`, the common case), leave this
    #: unset — `triggers` auto-derives from `consumes` instead, and a
    #: `Consume` that should feed inputs without also waking the agent uses
    #: `Consume(..., wakes=False)` rather than a second, hand-maintained list.
    triggers: list[Trigger] = []
    # Run priority within a single generation: lower value runs earlier.
    # Useful for "finishers"/evaluators that logically run last (§24).
    priority: int = 0
    # Max parallel executions of this agent within a generation. Leave None for
    # the runtime default (max_concurrency). Use it to throttle LLM-bound
    # producers (rate limits) independently of cheap I/O (file reads).
    concurrency_limit: int | None = None
    #: Whether the runtime auto-schedules this agent from matching events.
    #: `False` keeps it out of reactive scheduling (run it explicitly).
    wakes: bool | Callable[[Event, Context | None], bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        legacy = cls.__dict__.get("produces")
        if legacy is not None and "steps" not in cls.__dict__:
            cls.steps = legacy

    def __init__(
        self,
        name: str | None = None,
        triggers: list[Trigger] | None = None,
        priority: int | None = None,
        *,
        steps: Sequence[Step[Any]] | None = None,
        wakes: bool | Callable[[Event, Context | None], bool] | None = None,
    ):
        self.name = name or self.name or self.__class__.__name__

        self.priority = (
            priority if priority is not None else getattr(self.__class__, "priority", 0)
        )

        existing_steps = self.__dict__.get("steps")
        existing_produces = self.__dict__.get("produces")
        resolved: Sequence[Step[Any]] | None
        if steps is not None:
            resolved = steps
        elif existing_steps is not None:
            resolved = existing_steps
        elif existing_produces is not None:
            resolved = existing_produces
        else:
            resolved = getattr(self.__class__, "steps", None)
            if resolved is None:
                resolved = getattr(self.__class__, "produces", None)
        self.steps = list(resolved) if resolved is not None else None
        # Deprecated instance-level alias: keep it in sync with `steps`.
        self.produces = self.steps

        if triggers is not None:
            self.triggers = list(triggers)
        elif self.triggers:
            self.triggers = list(self.triggers)
        elif self.consumes is not None:
            self.triggers = self._generate_triggers_from_consumes()
        elif self.steps:
            self.triggers = self._generate_triggers_from_steps()
        else:
            self.triggers = []

        self.wakes = (
            wakes if wakes is not None else getattr(self.__class__, "wakes", True)
        )

        self._validate_contracts()

    def _generate_triggers_from_consumes(self) -> list[Trigger]:
        result = []
        for c in self.consumes or []:
            result.extend(c.to_triggers())
        return result

    def _generate_triggers_from_steps(self) -> list[Trigger]:
        """Derive the agent's wake set from its steps' `consumes` contracts."""
        result: list[Trigger] = []
        for step in self.steps or []:
            for consume in step.consume_specs() or ():
                result.extend(consume.to_triggers())
        return result

    def _validate_contracts(self) -> None:
        if self.consumes is not None:
            for c in self.consumes:
                if not isinstance(c, Consume):
                    raise TypeError(
                        f"Agent {self.name!r}: consumes must contain Consume "
                        f"instances, got {type(c)}"
                    )
        if self.steps is not None:
            for p in self.steps:
                if not isinstance(p, Step):
                    raise TypeError(
                        f"Agent {self.name!r}: steps must contain Step "
                        f"instances, got {type(p)}"
                    )

    def matches(self, event: Event, context: Context | None = None) -> bool:
        if callable(self.wakes):
            if not self.wakes(event, context):
                return False
        elif not self.wakes:
            return False
        return bool(self.matching_triggers(event, context))

    def matching_triggers(
        self, event: Event, context: Context | None = None
    ) -> list[Trigger]:
        """Every trigger that matches `event` — `matches()` is just
        `bool(...)` of this. `Runtime._arun_once_impl` uses the full list
        (not just the bool) to decide whether *every* matching trigger asks
        for debouncing (`Trigger.debounce`) before collapsing repeat events
        into one run."""
        return [trigger for trigger in self.triggers if trigger.matches(event, context)]

    def collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Public access to the consumed artifacts.

        Used by the runtime to record the reads linkage (provenance) on run.
        When the agent has no explicit `consumes`, this is the union of its
        steps' own `consumes`.
        """
        if self.consumes is None and self.steps:
            inputs: list[Artifact[Any]] = []
            seen: set[str] = set()
            for step in self.steps:
                for artifact in step.collect_inputs(context):
                    if artifact.id in seen:
                        continue
                    seen.add(artifact.id)
                    inputs.append(artifact)
            return inputs
        return self._collect_inputs(context)

    def _collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Collects all artifacts matching consumes and conditions.

        Ranking/truncation, if any, is a `Runtime`-level policy
        (`context.resources.context_builder`, see `context_builder.py`), not
        this agent's — applied here so both this and `collect_inputs()`
        (used by the runtime for provenance) see the identical, already
        built list.
        """
        if not self.consumes:
            return []
        inputs: list[Artifact[Any]] = []
        for c in self.consumes:
            inputs.extend(c.collect(context))
        builder = context.resources.context_builder
        if builder is not None:
            inputs = builder.build(context, self, inputs)
        return inputs

    async def run(self, event: Event, context: Context) -> Patch | None:
        """Default: runs `self.steps` via `execute()` (the effects-first
        path — write a `Step` subclass or `@step` function instead of
        overriding this).

        Overriding `run()` to return a `Patch` by hand is a low-level,
        internal escape hatch for cases `effects` genuinely can't express —
        not a third everyday produce style (see `reactifact.produce`'s module
        docstring for the two you should reach for first). No example in
        this repo overrides it; only this repo's own tests do.
        """
        await self.execute(context, event)
        return None

    @staticmethod
    def _input_cursor(
        step: Step[Any],
        artifacts: list[Artifact[Any]],
        scope_value: Any,
    ) -> dict[type, dict[str, int]]:
        """The step's input view: {type: {artifact id: version}}."""
        cursor: dict[type, dict[str, int]] = {}
        for artifact in artifacts:
            if not _scope_matches(step.scope, scope_value, artifact):
                continue
            cursor.setdefault(type(artifact.data), {})[artifact.id] = artifact.version
        return cursor

    async def execute(self, context: Context, event: Event | None = None) -> None:
        """Runs the agent's produces (usually on an event).

        Effects-first (§24): class steps write `effects.*`, function steps `call.effects.*`
        and return None; the *runtime* compiles the effect slot into one
        atomic patch. This method
        only *runs* the produces — it does not build a patch. (`run` remains the
        agent-level escape hatch for custom Agent subclasses that assemble a
        change-set by hand; the runtime merges its result after the effects.)
        """
        if not self.steps:
            return None
        inputs = self._collect_inputs(context)
        slot = current_effects()
        log = _produce_runs.get()
        for p in self.steps:
            runs, trigger = self._resolve_produce_call(p, event, context)
            if not runs:
                continue
            step_inputs = p.collect_inputs(context) if p.consumes else inputs
            scope_value = _scope_value(p.scope, trigger)
            cursor_artifacts = (
                [*step_inputs, trigger] if trigger is not None else step_inputs
            )
            input_cursor = self._input_cursor(p, cursor_artifacts, scope_value)
            cursor_key = (self.name, p.name, scope_value)
            trigger_cursor = (
                {type(trigger.data): {trigger.id: trigger.version}}
                if trigger is not None
                else {}
            )
            if (
                trigger_cursor
                and context._step_cursors.get(cursor_key) == trigger_cursor
            ):
                continue
            before = len(slot.operations) if slot is not None else 0
            started_at = datetime.now(UTC)
            started = time.monotonic()
            call = StepCall(
                context=context,
                inputs=step_inputs,
                event=event,
                trigger=trigger,
                scope=scope_value,
                cursor={t: dict(v) for t, v in input_cursor.items()},
            )
            produce_token = set_current_produce(p.name)
            context_token = set_step_context(context)
            call_token = set_step_call(call)
            try:
                result = await invoke_step(p, call)
                p._apply_result(result, call.effects)
                if trigger_cursor:
                    context._step_cursors[cursor_key] = trigger_cursor
            finally:
                reset_step_call(call_token)
                reset_step_context(context_token)
                reset_current_produce(produce_token)
                # Record the produce even when it raises — a failing produce is
                # exactly what a trace should show (the span is then the error
                # span, with this produce's partial operations/latency).
                if log is not None:
                    operations = (
                        len(slot.operations) if slot is not None else 0
                    ) - before
                    log.append(
                        (
                            p.name,
                            operations,
                            (time.monotonic() - started) * 1000,
                            started_at,
                        )
                    )
        return None

    @staticmethod
    def _resolve_produce_call(
        p: Step[Any], event: Event | None, context: Context
    ) -> tuple[bool, Artifact[Any] | None]:
        """Whether to call `p.run()` for `event`, and the triggering
        artifact to put on the `StepCall.trigger` field (see `Step`'s
        docstring, and `StepCall`'s, for the full contract).

        `p.reacts_to is None` (the default): unrestricted, matching the
        pre-`reacts_to` behavior where every produce ran on every event this
        agent got — `trigger` is still resolved on a best-effort basis (for
        a produce that wants it without narrowing `reacts_to`), but never a
        reason to skip the call, since there's no per-type contract to hold
        it to.

        `p.reacts_to` set: exact type equality against `event.artifact_type`
        (same convention as `Trigger.artifact_type`, not `issubclass` — that
        one's a `Context.list_artifacts()` convention for querying by base
        type). For a CREATED/UPDATED/STALE event, also requires
        `context.get(event.artifact_id)` to still resolve — the artifact may
        have been deleted by another agent earlier in the same generation
        (the same race `Trigger.matches()` documents) — so `reacts_to`, once
        set, guarantees `call.trigger` is always a live, correctly-typed
        artifact. A DELETED event is exempt from that liveness requirement:
        `context.get(...)` correctly returning `None` *is* the event there,
        not a race, so the produce still runs with `trigger=None` —
        deletion-reacting code is expected to handle that itself.
        """
        consumes = p.consume_specs()
        if event is None:
            return (consumes is None), None
        if consumes is not None:
            matched = any(
                trigger.matches(event, context)
                for consume in consumes
                for trigger in consume.to_triggers()
            )
            if not matched:
                return False, None
        trigger = context.get(event.artifact_id)
        if (
            consumes is not None
            and trigger is None
            and event.type is not EventType.ARTIFACT_DELETED
        ):
            return False, None
        return True, trigger


def create_agent(
    name: str,
    *,
    consumes: Sequence[Consume] | None = None,
    steps: Sequence[Step[Any]] | None = None,
    produces: Sequence[Step[Any]] | None = None,
    capabilities: tuple[str, ...] = (),
    priority: int = 0,
    concurrency_limit: int | None = None,
    triggers: list[Trigger] | None = None,
    wakes: bool | Callable[[Event, Context | None], bool] = True,
) -> Agent:
    """Builds an Agent instance without subclassing.

    `Agent` is a container — nothing needs overriding in the common case — so a
    subclass is only ceremony. This constructor-style builder covers all of the
    declarative knobs:

    ```
    echo = create_agent(
        name="echo",
        consumes=[Consume(Question)],
        steps=[echo_step],                # a Step or @step(...) function
    )
    ```

    Falls back to `name` defaults the same way as `Agent.__init__`. Pass
    `triggers` only for the rare imperative style with no `consumes` at all
    (see `Agent.triggers`'s own docstring) — the ordinary declarative case
    should leave it unset and use `Consume(..., wakes=False)` instead.
    """
    if steps is not None and produces is not None:
        raise TypeError(
            "Pass either steps= or produces=, not both: produces is the deprecated alias for steps."
        )
    resolved_steps = steps if steps is not None else produces
    agent = Agent(
        name=name,
        triggers=triggers,
        priority=priority,
        steps=resolved_steps,
        wakes=wakes,
    )
    if consumes is not None:
        agent.consumes = consumes
        if triggers is None:
            agent.triggers = agent._generate_triggers_from_consumes()
    agent.capabilities = capabilities
    agent.concurrency_limit = concurrency_limit
    agent._validate_contracts()
    return agent
