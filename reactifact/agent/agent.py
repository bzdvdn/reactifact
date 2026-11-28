from __future__ import annotations

import asyncio
import os
import time
import warnings
from abc import ABC
from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, cast

from ..artifacts import Artifact, ArtifactConflict
from ..authz import AuthorizationError
from ..consume import Consume
from ..context import Context
from ..effects import Effects, current_effects
from ..events import Event, EventType
from ..guardrails import GuardrailViolation
from ..patches import Create, Link, Patch
from ..policy import RetryPolicy
from ..provenance import DERIVED_FROM
from ..tools import reset_current_produce, set_current_produce
from ..triggers import Trigger
from .step.call import (
    StepCall,
    reset_step_call,
    reset_step_context,
    set_step_call,
    set_step_context,
)
from .step.core import Step, _scope_matches, _scope_value
from .step.resolve import invoke_step

#: Per-task log of the produces that ran for the current agent execution:
#: ``(produce_class_name, operations_authored, latency_ms, started_at)``. ``Runtime._execute``
#: opens it (and later records the span), ``Agent.execute`` appends — a
#: contextvar so concurrent executions sharing one ``Agent`` instance never race.
_produce_runs: ContextVar[list[tuple[str, int, float, datetime]] | None] = ContextVar(
    "reactifact_produce_runs", default=None
)


def _dev_mode() -> bool:
    """Whether the explicit-`consumes` development warning is enabled.

    Opt in with `REACTIFACT_DEV=1` (or `REACTIFACT_ENV=dev`); production runs
    stay quiet.
    """
    raw = os.getenv("REACTIFACT_DEV")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("REACTIFACT_ENV", "").strip().lower() in {
        "dev",
        "development",
        "local",
    }


def begin_produce_runs() -> Token[Any]:
    """Opens the per-task produce-run log (see ``_produce_runs``)."""
    return _produce_runs.set([])


def produce_runs() -> list[tuple[str, int, float, datetime]]:
    """The produce runs recorded for the current task (empty if none)."""
    return _produce_runs.get() or []


def end_produce_runs(token: Token[Any]) -> None:
    _produce_runs.reset(token)


def _apply_provenance(
    step: Step[Any],
    call: StepCall,
    slot: Effects,
    start: int,
) -> None:
    """Links artifacts this step created to the inputs it actually resolved.

    Declared via ``@step(provenance=...)``; the relation points from the new
    artifact to each input, matching the direction of
    ``calculated_from``/``supported_by``. An explicit link between the same
    pair is left alone, so hand-written provenance always wins.
    """
    if not call.used or not step.provenance:
        return
    relation = (
        str(step.provenance) if isinstance(step.provenance, str) else DERIVED_FROM
    )
    operations = slot.operations[start:]
    created = [
        op for op in operations if isinstance(op, Create) and (op.id or op.artifact_id)
    ]
    if not created:
        return
    explicit = {
        (op.artifact_id, op.target_id) for op in operations if isinstance(op, Link)
    }
    for op in created:
        source_id = op.id or op.artifact_id
        assert source_id is not None
        for artifact in call.used:
            if artifact.id == source_id or (source_id, artifact.id) in explicit:
                continue
            slot.operations.append(Link(source_id, relation, artifact.id))


class Agent(ABC):  # noqa: B024 — interface without abstract methods, run() has a default
    """A group of steps sharing trigger, retry and timeout.

    Steps are the model; an `Agent` is the operational unit that answers
    "wake these together, retry them together, trace them under one name".
    `Runtime` accepts a list of agents, `Agent(...)` builds one without
    subclassing, and `Agent`/`StepGroup` remain compatibility aliases.

    If `run` is not overridden, the group collects inputs according to
    `consumes` and calls every step, merging the patches into one commit.
    A failed group may declare `fallback=` (one `Step`) and
    `fallback_on`/`fallback_skip`; it runs after retries in a fresh effects
    slot, so the failed attempt's effects never commit.
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
    #: Retry policy for this agent's execution (class or instance level).
    #: A retry gets a fresh `Effects` slot; only the successful patch commits.
    retry: RetryPolicy | None = None
    #: Hard per-execution deadline, in seconds. The runtime cancels the agent
    #: when it passes; `asyncio.TimeoutError` then follows the retry policy.
    timeout: float | None = None
    #: Whether the runtime auto-schedules this agent from matching events.
    #: `False` keeps it out of reactive scheduling (run it explicitly).
    wakes: bool | Callable[[Event, Context | None], bool] = True
    #: Optional fallback step run after the group's retries are exhausted.
    #: It writes into a fresh effects slot; the failed attempt's effects are
    #: already discarded by the runtime.
    fallback_step: Step[Any] | None = None
    #: Exception types that may trigger `fallback_step`.
    fallback_on: tuple[type[BaseException], ...] = (Exception,)
    #: Exception types that must never be degraded to a fallback artifact.
    fallback_skip: tuple[type[BaseException], ...] = (
        AuthorizationError,
        GuardrailViolation,
        ArtifactConflict,
    )

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
        consumes: Any | None = None,
        wakes: bool | Callable[[Event, Context | None], bool] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
        fallback: Step[Any] | None = None,
        fallback_on: Sequence[type[BaseException]] | None = None,
        fallback_skip: Sequence[type[BaseException]] | None = None,
        concurrency_limit: int | None = None,
        capabilities: tuple[str, ...] | None = None,
    ):
        consumes = Step._normalize_consumes(consumes)
        self.name = name or self.name or self.__class__.__name__

        self.priority = (
            priority if priority is not None else getattr(self.__class__, "priority", 0)
        )
        self.retry = (
            retry if retry is not None else getattr(self.__class__, "retry", None)
        )
        self.timeout = (
            timeout if timeout is not None else getattr(self.__class__, "timeout", None)
        )
        if self.retry is not None and not isinstance(self.retry, RetryPolicy):
            raise TypeError("Agent.retry must be a RetryPolicy or None")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("Agent.timeout must be > 0")

        declared_steps: list[Step[Any]] = []
        for base in reversed(type(self).__mro__):
            for attr, value in vars(base).items():
                if isinstance(value, Step) and getattr(type(self), attr, None) is value:
                    step_value = cast("Step[Any]", value)
                    bound = step_value.bind(self)
                    setattr(self, attr, bound)
                    declared_steps.append(bound)

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
        if declared_steps:
            self.steps = [*declared_steps, *(self.steps or [])]
        if self.steps is not None and not self.steps and type(self).run is Agent.run:
            raise ValueError("Agent requires at least one step")
        # Deprecated instance-level alias: keep it in sync with `steps`.
        self.produces = self.steps

        if consumes is not None:
            self.consumes = list(consumes)

        explicit_triggers = triggers is not None or bool(
            getattr(type(self), "triggers", None)
        )
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

        if (
            _dev_mode()
            and self.consumes is None
            and self.steps
            and not explicit_triggers
        ):
            warnings.warn(
                f"Agent {self.name!r} declares steps but no consumes; the runtime "
                "will derive its wake set from step reads. Pass consumes=[...] "
                "explicitly (or triggers= for the imperative form). Set "
                "REACTIFACT_DEV=0 to silence this warning.",
                UserWarning,
                stacklevel=2,
            )

        self.wakes = (
            wakes if wakes is not None else getattr(self.__class__, "wakes", True)
        )
        self.concurrency_limit = (
            concurrency_limit
            if concurrency_limit is not None
            else getattr(self.__class__, "concurrency_limit", None)
        )
        self.capabilities = (
            tuple(capabilities)
            if capabilities is not None
            else getattr(self.__class__, "capabilities", ())
        )

        self.fallback_step = (
            fallback
            if fallback is not None
            else getattr(self.__class__, "fallback_step", None)
        )
        if self.fallback_step is not None and not isinstance(self.fallback_step, Step):
            raise TypeError("Agent.fallback must be a Step or None")
        self.fallback_on = tuple(
            fallback_on
            if fallback_on is not None
            else getattr(self.__class__, "fallback_on", (Exception,))
        )
        self.fallback_skip = tuple(
            fallback_skip
            if fallback_skip is not None
            else getattr(
                self.__class__,
                "fallback_skip",
                (AuthorizationError, GuardrailViolation, ArtifactConflict),
            )
        )
        self._validate_contracts()

    def agents(self) -> list[Agent]:
        """The agent itself, for `Route`/`Pipeline` expansion."""
        return [self]

    def groups(self) -> list[Agent]:
        """Deprecated alias for `agents()`."""
        return self.agents()

    def should_fallback(self, error: BaseException) -> bool:
        """Whether `error` may be degraded through `fallback`."""
        return (
            self.fallback_step is not None
            and isinstance(error, self.fallback_on)
            and not isinstance(error, self.fallback_skip)
        )

    async def run_fallback(self, context: Context, event: Event | None) -> None:
        """Runs the fallback step in the current effects slot.

        Called by `Runtime` after the normal attempts failed and their effects
        were discarded. The fallback is intentionally a single `Step`: a
        multi-step recovery belongs in an explicit group, not hidden here.
        """
        if self.fallback_step is None:
            return None
        fallback_group = Agent(
            name=f"{self.name}:fallback",
            steps=[self.fallback_step],
        )
        await fallback_group.execute(context, event)

    def _generate_triggers_from_consumes(self) -> list[Trigger]:
        result = []
        for c in self.consumes or []:
            result.extend(c.to_triggers())
        return result

    def _generate_triggers_from_steps(self) -> list[Trigger]:
        """Derive the agent's wake set from its steps' `consumes` contracts."""
        result: list[Trigger] = []
        for step in self.steps or []:
            for consume in step.read_specs() or ():
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
        if self.fallback_step is not None and not isinstance(self.fallback_step, Step):
            raise TypeError(
                f"Agent {self.name!r}: fallback must be a Step, "
                f"got {type(self.fallback_step)}"
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
        everyday code writes a `Step` subclass or an `@step` function instead.
        No example in this repo overrides it; only this repo's own tests do.
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
        group-level escape hatch for custom Agent subclasses that assemble a
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
            step_inputs = p.collect_inputs(context) if p.reads else inputs
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
                if p.timeout is not None:
                    async with asyncio.timeout(p.timeout):
                        result = await invoke_step(p, call)
                else:
                    result = await invoke_step(p, call)
                p._apply_result(result, call.effects, call)
                if p.provenance and slot is not None:
                    _apply_provenance(p, call, slot, before)
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
        reads = p.read_specs()
        if event is None:
            return (reads is None), None
        if reads is not None:
            matched = any(
                trigger.matches(event, context)
                for consume in reads
                for trigger in consume.to_triggers()
            )
            if not matched:
                return False, None
        trigger = context.get(event.artifact_id)
        if (
            reads is not None
            and trigger is None
            and event.type is not EventType.ARTIFACT_DELETED
        ):
            return False, None
        return True, trigger


def create_step_group(
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
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
    fallback: Step[Any] | None = None,
    fallback_on: Sequence[type[BaseException]] | None = None,
    fallback_skip: Sequence[type[BaseException]] | None = None,
) -> Agent:
    """Builds an `Agent` without subclassing (compatibility constructor).

    A group is only needed when steps should share a trigger, retry policy,
    timeout, priority or concurrency limit; a single step can be passed to the
    runtime on its own. This constructor-style builder covers the declarative
    knobs:

    ```
    analysis = Agent(
        "analysis",
        consumes=[Consume(Question)],
        retry=RetryPolicy(max_attempts=3),
        steps=[compute_spend, compute_variance],
        fallback=FallbackAnswer("Could not compute"),
    )
    ```

    `fallback=` is one `Step` run after the group's retries are exhausted,
    in a fresh effects slot (the failed attempt is discarded). `fallback_on=`
    selects exception types; `fallback_skip=` protects authz/guardrail/conflict
    errors from being degraded.

    Pass `triggers` only for the rare imperative style with no `consumes` at
    all (see
    `Agent.triggers`'s own docstring) — the ordinary declarative case
    should leave it unset and use `Consume(..., wakes=False)` instead.
    """
    if steps is not None and produces is not None:
        raise TypeError(
            "Pass either steps= or produces=, not both: produces is the deprecated alias for steps."
        )
    resolved_steps = steps if steps is not None else produces
    group = Agent(
        name=name,
        triggers=triggers,
        priority=priority,
        steps=resolved_steps,
        consumes=consumes,
        wakes=wakes,
        retry=retry,
        timeout=timeout,
        fallback=fallback,
        fallback_on=fallback_on,
        fallback_skip=fallback_skip,
    )
    group.capabilities = capabilities
    group.concurrency_limit = concurrency_limit
    group._validate_contracts()
    return group


def normalize_units(
    *,
    steps: list[Any] | None = None,
    agents: list[Any] | None = None,
    groups: list[Any] | None = None,
) -> list[Agent]:
    """Normalizes the constructor keywords into dispatch units.

    `steps=` accepts only `Step` and wraps each in an `Agent` of one, copying
    `priority`/`concurrency_limit`; `agents=` accepts only `Agent`.
    Result order is steps, then agents, preserving each list.
    """
    if agents is not None and groups is not None:
        raise TypeError("Pass only one of agents= or groups=")
    declared_agents = agents if agents is not None else groups

    def check_agents(items: list[Any], keyword: str) -> list[Agent]:
        checked: list[Agent] = []
        for item in items:
            if isinstance(item, Step):
                raise TypeError(
                    f"{keyword}= accepts Agent, not Step; pass raw steps "
                    "to steps=[...] instead"
                )
            if not isinstance(item, Agent):
                raise TypeError(
                    f"{keyword}= expects Agent instances, got {type(item).__name__}"
                )
            checked.append(item)
        return checked

    merged: list[Agent] = []
    if steps is not None:
        for item in steps:
            if isinstance(item, Agent):
                raise TypeError(
                    "steps= accepts Step, not Agent; pass agents=[...] "
                    "for explicit agents"
                )
            if not isinstance(item, Step):
                raise TypeError(
                    f"steps= expects Step instances, got {type(item).__name__}"
                )
            group = Agent(name=item.name, steps=[item], priority=item.priority)
            group.concurrency_limit = item.concurrency_limit
            group.retry = item.retry
            merged.append(group)
    if declared_agents is not None:
        merged.extend(check_agents(declared_agents, "agents"))
    return merged


#: Deprecated compatibility alias; new code uses `Agent`.
StepGroup = Agent
