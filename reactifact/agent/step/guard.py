"""Step guards: dynamic run conditions around input resolution.

A guard is evaluated in two phases:

- ``pre`` — before the runtime resolves the step's typed inputs. It sees
  ``context``/``event``/``trigger`` and is the right place for cheap checks
  such as "the target id is still absent" (so a missing input is not reported
  for a step that was not supposed to run).
- ``post`` — after the step's inputs were collected, before ``run``. It sees the
  `StepInvocation` (including `inputs`) and is the right place for checks that
  depend on what is actually available.

Guards are pure: they may read the context, never write effects. Return
`GuardResult` (or a bool from a custom guard).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from ...artifacts import Artifact, ArtifactKey
from ...context import Context
from ...events import Event, EventType
from .invocation import StepInvocation


@dataclass(frozen=True)
class GuardResult:
    """The decision of one guard evaluation."""

    allowed: bool
    reason: str = ""
    code: str = "guard"

    @classmethod
    def permit(cls, reason: str = "") -> GuardResult:
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str, *, code: str = "guard") -> GuardResult:
        return cls(False, reason, code)


class Guard:
    """Base class for a step run condition.

    Subclasses override `pre` and/or `post`; the default is "always allow".
    The static factories (`Guard.idempotent`, `Guard.field`, `Guard.ready`,
    `Guard.all`, …) build the common guards.
    """

    async def pre(
        self,
        context: Context,
        event: Event | None,
        trigger: Artifact[Any] | None,
    ) -> GuardResult:
        return GuardResult.permit()

    async def post(self, invocation: StepInvocation) -> GuardResult:
        return GuardResult.permit()

    @staticmethod
    def idempotent(
        key: ArtifactKey[Any] | str | Callable[[Artifact[Any]], str | None],
    ) -> Guard:
        """Run only while the key's target artifact does not exist."""
        return _Idempotent(key)

    @staticmethod
    def field(field: str, value: Any) -> Guard:
        """Run only when the trigger artifact's data field equals `value`."""
        return _Field(field, value)

    @staticmethod
    def event(*event_types: EventType) -> Guard:
        """Run only for one of the given event types."""
        return _Event(event_types)

    @staticmethod
    def has(*types: type[BaseModel]) -> Guard:
        """Run only when at least one of the input artifact types is present."""
        return _Present(types, require_all=False)

    @staticmethod
    def ready(*types: type[BaseModel]) -> Guard:
        """Run only when every one of the input artifact types is present."""
        return _Present(types, require_all=True)

    @staticmethod
    def custom(
        fn: Callable[..., Any],
        *,
        phase: Literal["pre", "post"] = "pre",
        code: str = "custom",
    ) -> Guard:
        """A custom sync/async guard.

        `phase="pre"` calls `fn(context, event, trigger)`;
        `phase="post"` calls `fn(invocation)`. Return a bool or `GuardResult`.
        """
        return _Custom(fn, phase=phase, code=code)

    @staticmethod
    def all(*guards: Guard) -> Guard:
        return All(*guards)

    @staticmethod
    def any(*guards: Guard) -> Guard:
        return AnyOf(*guards)

    @staticmethod
    def not_(guard: Guard) -> Guard:
        return Not(guard)


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _as_result(value: Any, *, code: str) -> GuardResult:
    if isinstance(value, GuardResult):
        return value
    if value is None:
        return GuardResult.permit()
    if isinstance(value, bool):
        return (
            GuardResult.permit()
            if value
            else GuardResult.deny("guard returned False", code=code)
        )
    raise TypeError(
        f"guard returned {type(value).__name__}; expected bool or GuardResult"
    )


class All(Guard):
    """Every guard must allow; the first denial wins."""

    def __init__(self, *guards: Guard) -> None:
        self.guards = tuple(guards)

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        for guard in self.guards:
            result = await guard.pre(context, event, trigger)
            if not result.allowed:
                return result
        return GuardResult.permit()

    async def post(self, invocation: StepInvocation) -> GuardResult:
        for guard in self.guards:
            result = await guard.post(invocation)
            if not result.allowed:
                return result
        return GuardResult.permit()


class AnyOf(Guard):
    """At least one guard must allow."""

    def __init__(self, *guards: Guard) -> None:
        self.guards = tuple(guards)

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        reasons: list[str] = []
        for guard in self.guards:
            result = await guard.pre(context, event, trigger)
            if result.allowed:
                return result
            reasons.append(result.reason)
        return GuardResult.deny(
            "; ".join(reason for reason in reasons if reason) or "no guard allowed"
        )

    async def post(self, invocation: StepInvocation) -> GuardResult:
        reasons: list[str] = []
        for guard in self.guards:
            result = await guard.post(invocation)
            if result.allowed:
                return result
            reasons.append(result.reason)
        return GuardResult.deny(
            "; ".join(reason for reason in reasons if reason) or "no guard allowed"
        )


class Not(Guard):
    """Invert one guard."""

    def __init__(self, guard: Guard) -> None:
        self.guard = guard

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        result = await self.guard.pre(context, event, trigger)
        if result.allowed:
            return GuardResult.deny("negated guard allowed", code=result.code)
        return GuardResult.permit(result.reason)

    async def post(self, invocation: StepInvocation) -> GuardResult:
        result = await self.guard.post(invocation)
        if result.allowed:
            return GuardResult.deny("negated guard allowed", code=result.code)
        return GuardResult.permit(result.reason)


class _Idempotent(Guard):
    def __init__(
        self, key: ArtifactKey[Any] | str | Callable[[Artifact[Any]], str | None]
    ) -> None:
        self.key = key

    def _target(self, trigger: Artifact[Any] | None) -> str | None:
        if isinstance(self.key, ArtifactKey):
            return self.key.from_artifact(trigger) if trigger is not None else None
        if isinstance(self.key, str):
            return self.key
        return self.key(trigger) if trigger is not None else None

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        target = self._target(trigger)
        if target is None:
            return GuardResult.deny("no idempotency target", code="idempotent")
        if context.get(target) is not None:
            return GuardResult.deny(
                f"target {target!r} already exists", code="idempotent"
            )
        return GuardResult.permit(f"target {target!r} is absent")


class _Field(Guard):
    def __init__(self, field: str, value: Any) -> None:
        self._field = field
        self._value = value

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        if trigger is None:
            return GuardResult.deny("no trigger artifact", code="field")
        actual = getattr(trigger.data, self._field, None)
        if actual == self._value:
            return GuardResult.permit()
        return GuardResult.deny(
            f"{type(trigger.data).__name__}.{self._field}={actual!r}, expected {self._value!r}",
            code="field",
        )


class _Event(Guard):
    def __init__(self, event_types: Sequence[EventType]) -> None:
        self.event_types = tuple(event_types)

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        if event is not None and event.type in self.event_types:
            return GuardResult.permit()
        return GuardResult.deny(
            f"event {getattr(event, 'type', None)!r} is not in {self.event_types!r}",
            code="event",
        )


class _Present(Guard):
    def __init__(self, types: Sequence[type[BaseModel]], *, require_all: bool) -> None:
        self.types = tuple(types)
        self.require_all = require_all

    async def post(self, invocation: StepInvocation) -> GuardResult:
        artifacts = [*invocation.inputs]
        if invocation.trigger is not None:
            artifacts.append(invocation.trigger)

        def has(type_: type[BaseModel]) -> bool:
            return any(isinstance(artifact.data, type_) for artifact in artifacts)

        if self.require_all:
            missing = [type_.__name__ for type_ in self.types if not has(type_)]
            if missing:
                return GuardResult.deny(
                    f"missing inputs: {', '.join(missing)}", code="present"
                )
            return GuardResult.permit()
        if any(has(type_) for type_ in self.types):
            return GuardResult.permit()
        names = ", ".join(type_.__name__ for type_ in self.types)
        return GuardResult.deny(f"none of {names} is present", code="present")


class _Custom(Guard):
    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        phase: Literal["pre", "post"],
        code: str,
    ) -> None:
        self.fn = fn
        self.phase = phase
        self.code = code

    async def pre(
        self, context: Context, event: Event | None, trigger: Artifact[Any] | None
    ) -> GuardResult:
        if self.phase != "pre":
            return GuardResult.permit()
        return _as_result(
            await _resolve(self.fn(context, event, trigger)), code=self.code
        )

    async def post(self, invocation: StepInvocation) -> GuardResult:
        if self.phase != "post":
            return GuardResult.permit()
        return _as_result(await _resolve(self.fn(invocation)), code=self.code)


def normalize_guard(
    guard: Guard | Sequence[Guard] | Callable[..., Any] | None,
) -> Guard | None:
    """Normalize a step's guard declaration into one `Guard`."""
    if guard is None:
        return None
    if isinstance(guard, Guard):
        return guard
    if isinstance(guard, Sequence) and not isinstance(guard, (str, bytes)):
        items = [
            normalized
            for item in guard
            if (normalized := normalize_guard(item)) is not None
        ]
        return All(*items)
    if callable(guard):
        return Guard.custom(guard)
    raise TypeError("guard must be a Guard, a sequence of Guards, or a callable")


__all__ = [
    "All",
    "AnyOf",
    "Guard",
    "GuardResult",
    "Not",
    "normalize_guard",
]
