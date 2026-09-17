"""reactifact.produce — how a produce writes an artifact (§24).

Two styles cover almost everything and are the two canonical ones — reach
for one of these first:

- **Subclass + effects** — `class X(Produce[Model]): async def produce(self,
  context, inputs, event=None): self.effects.create(...); return None`. Use
  this whenever the produce has its own state-free logic worth naming as a
  class (the common case in every example under `examples/`).
- **`@produce(Model)` function** — `@produce(Model)\\ndef f(context, inputs,
  effects): effects.create(...)`, or the plain return-style
  `def f(context, inputs): return Model(...)` (also accepts `event`). One
  decorator, both signatures recognized by parameter name — pick this for a
  short, one-off produce with no class ceremony.

One other thing `Produce`/`Agent` accepts is *not* on that list on purpose:

- Overriding `Agent.run(self, event, context) -> Patch | None` directly,
  bypassing `Produce` entirely (though not necessarily `effects` — the
  runtime still merges whatever `current_effects()` collected during the
  call, same as for a normal produce). This is a low-level, internal escape
  hatch for cases a `Produce` genuinely can't express — not a third everyday
  style to reach for on a first pass. `reactifact.llm_agent.StructuredGenerateAgent`
  is the one built-in exception (writes via `current_effects()` directly
  instead of `self.effects`, since `Agent` — unlike `Produce` — has no
  `effects` property); no example under `examples/` overrides `run()`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from typing import Any, Generic, TypeVar, get_args, get_origin

from pydantic import BaseModel

from .artifacts import Artifact, ArtifactType
from .context import Context
from .effects import Effects
from .events import Event
from .patches import Patch

TOut = TypeVar("TOut", bound=BaseModel)


class Produce(Generic[TOut]):
    """Describes the produced artifact type and how it is created.

    `artifact_type` is auto-derived from the generic when a subclass is written
    as `class X(Produce[Foo])` — write it explicitly only to override or when
    the class has no generic (e.g. programmatic `Produce(Foo)`).

    A produce whose `produce()` body legitimately creates more than one
    artifact type declares the extra ones in `also_creates` — either as a
    class attribute (`also_creates = (Bar, Baz)`) or a constructor argument
    (`Produce(Foo, also_creates=[Bar])`). `Runtime._validate_patch_types`
    checks every `Create` op an agent's generation produces against the union
    of `artifact_type`/`also_creates` across that agent's `produces` list —
    without declaring `Bar` here, a `self.effects.create(Bar(...))` inside
    `artifact_type = Foo`'s own `produce()` raises "not declared in produces"
    at commit time, since nothing recorded that this produce is allowed to
    write it. Before `also_creates` existed, the only way to widen that set
    was an inert second `Produce(Bar)` placeholder added to the agent's
    `produces` list purely so its unused `artifact_type` got unioned in —
    correct, but nothing at the call site said why that placeholder was
    there; `also_creates` puts the declaration on the produce that actually
    does the writing.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "artifact_type" in cls.__dict__ or cls.artifact_type is not None:
            return
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is Produce:
                args = get_args(base)
                if args and isinstance(args[0], type):
                    cls.artifact_type = args[0]
                    return

    artifact_type: ArtifactType | None = None
    also_creates: tuple[ArtifactType, ...] = ()

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        *,
        also_creates: Sequence[ArtifactType] | None = None,
    ):
        self.artifact_type = artifact_type or self.__class__.artifact_type
        if self.artifact_type is None:
            raise ValueError(
                "artifact_type must be provided either as class attribute or constructor argument"
            )
        self.also_creates = (
            tuple(also_creates) if also_creates is not None else self.__class__.also_creates
        )

    @property
    def effects(self) -> Effects:
        """The produce-scoped effect slot (authoring surface, §24).

        Only meaningful *inside* `produce()`: the runtime pushes a fresh slot
        per execution. Returns an error outside a run.
        """
        from .effects import current_effects

        slot = current_effects()
        if slot is None:
            raise RuntimeError(
                "Produce.effects is only available while the runtime executes "
                "this produce — write effects inside produce(), not before it."
            )
        return slot

    async def produce(
        self,
        context: Context,
        inputs: list[Artifact[Any]],
        event: Event | None = None,
    ) -> None:
        """No-op by default (§24).

        Subclass-style overrides write `self.effects.*` and return None; a
        `None` return means "no work". A bare `Produce(Model)` (no override)
        is a valid, deliberate no-op — used e.g. to widen an agent's allowed
        `Create` types when the actual write happens via `self.effects.ask(...)`
        in another produce (see `examples/supervisor`).
        """
        return None

    def _apply_result(self, result: Any) -> None:
        """Writes a `@produce`-decorated function's return-style result into
        the effect slot (§24).

        `None` — nothing; a model or a list of models — creates; a Patch — a
        legacy escape (its operations are appended to the effects).
        """
        if result is None:
            return
        if isinstance(result, Patch):
            for op in result.operations:
                self.effects.add(op)
            return
        if isinstance(result, list):
            for item in result:
                self.effects.create(item)
        else:
            self.effects.create(result)


def produce(
    artifact_type: ArtifactType,
) -> Callable[[Callable[..., Any]], Produce[Any]]:
    """Decorator to create a Produce from a function.

    The function must accept ``(context, inputs)`` plus, optionally, ``event``
    and/or ``effects`` (each recognized by name):

    - ``def f(context, inputs)`` — return style (no event, no slot);
    - ``def f(context, inputs, event)`` — return style with the event;
    - ``def f(context, inputs, effects)`` — author the produce-scoped
      ``Effects`` slot directly (the same surface as ``self.effects``);
    - ``def f(context, inputs, event, effects)`` — both.

    Return style: return a model / a list of models / a ``Patch`` / ``None``;
    the runtime writes them into the slot (reate/append) as usual. Effects
    style: write ``effects.create/update/link/ask/...`` and return ``None`` —
    the function behaves exactly like a class produce with ``self.effects``.
    """

    def decorator(func: Callable[..., Any]) -> Produce[Any]:
        # Recognize which optional parameters the function declares.
        params = list(inspect.signature(func).parameters.values())
        names = [p.name for p in params]
        if len(params) < 2:
            raise TypeError(
                f"Function {func.__name__} must accept at least (context, inputs)"
            )
        accepts_event = "event" in names
        accepts_effects = "effects" in names

        class _FunctionProduce(Produce[Any]):
            async def produce(
                self,
                context: Context,
                inputs: list[Artifact[Any]],
                event: Event | None = None,
            ) -> None:
                args: list[Any] = [context, inputs]
                if accepts_event:
                    args.append(event)
                result = (
                    func(*args, effects=self.effects)
                    if accepts_effects
                    else func(*args)
                )
                if asyncio.iscoroutine(result):
                    result = await result
                self._apply_result(result)

        # Return an instance of the Produce class with the required artifact_type
        instance = _FunctionProduce(artifact_type=artifact_type)
        return instance

    return decorator
