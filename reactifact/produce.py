"""reactifact.produce — how a produce writes an artifact (§24).

Two styles cover almost everything and are the two canonical ones — reach
for one of these first:

- **Subclass + effects** — `class X(Produce[Model]): async def produce(self,
  call: ProduceCall) -> None: call.effects.create(...); return None`. Use
  this whenever the produce has its own state-free logic worth naming as a
  class (the common case in every example under `examples/`).
- **`@produce(Model)` function** — `@produce(Model)\\ndef f(call): call.effects
  .create(...)`, or the plain return-style `def f(call): return Model(...)`.
  One decorator, one argument shape either way — pick this for a short,
  one-off produce with no class ceremony.

Both styles take exactly one argument: a `ProduceCall`. Earlier versions
recognized a growing list of individually-named optional parameters
(`context`, `inputs`, `event`, `trigger`, `effects`) by inspecting the
function's signature — `ProduceCall` replaces that: one object, its fields
are what a produce can ever read (`.context`/`.inputs`/`.event`/`.trigger`)
plus the write-side `.effects`, all discoverable from an editor's
autocomplete on `call.` without chasing which combination of parameter names
does what. See `ProduceCall`'s own docstring for what each field is and when
it's populated.

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
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel

from .artifacts import Artifact, ArtifactType
from .context import Context
from .effects import Effects
from .events import Event
from .patches import Patch

TOut = TypeVar("TOut", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ProduceCall:
    """The single argument `produce()` receives, both class-style and
    `@produce`-decorated functions.

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
      CREATED/UPDATED/STALE — see `Produce`'s own docstring for the full
      contract (and its one exception, a DELETED event on the produce's own
      `reacts_to` type, where `None` here *is* the event, not a gap).
      Without `reacts_to`, still resolved and passed whenever `event` isn't
      `None`, but purely as a convenience — there's no per-type contract to
      enforce, so it's never a reason `Agent.execute()` skips the call.
    - `effects` — the produce-scoped `Effects` authoring surface (the same
      contextvar-backed slot as a class-style `Produce`'s `self.effects`;
      exposed here too since a `@produce`-decorated function has no `self`
      to hang it from).
    """

    context: Context
    inputs: list[Artifact[Any]]
    event: Event | None = None
    trigger: Artifact[Any] | None = None

    @property
    def effects(self) -> Effects:
        """See `Produce.effects` — same contextvar slot, same guard."""
        from .effects import current_effects

        slot = current_effects()
        if slot is None:
            raise RuntimeError(
                "ProduceCall.effects is only available while the runtime "
                "executes this produce — write effects inside produce(), "
                "not before it."
            )
        return slot


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

    The input-side mirror of that is `reacts_to`: an agent with several
    `consumes` and several `produces` runs *every* produce on *every*
    matching event by default (`Agent.execute()` has no idea which of an
    agent's several `Consume`s a given produce actually cares about) — every
    produce ends up guarding itself by hand, `if call.event is None or not
    isinstance(call.trigger.data, TheOneTypeICareAbout): return None`, at
    the top of its own body. Declaring `reacts_to = (TheType,)` (a tuple,
    same convention as `also_creates` — more than one entry for more than
    one type) moves that guard to where the intent already lives — the
    class declaration — and `Agent.execute()` simply skips calling
    `produce()` at all for an event none of `reacts_to` matches, the same
    way `Trigger.artifact_type` gates whether the *agent* wakes up at all.
    Two produces sharing one `artifact_type` (the same output) but different
    `reacts_to` (different triggers) is exactly why this can't just reuse
    `artifact_type` for both directions — see `examples`/product code with a
    `FinalizeWithDocuments`/`DirectFinalize`-shaped pair, both producing the
    same result type from two different upstream events. `None` (the
    default) means unrestricted — today's behavior, unchanged, so this is
    purely additive.

    Once `reacts_to` narrows *which* event a produce runs for, resolving
    that event's own artifact is still work every such produce repeats —
    `ProduceCall.trigger` does it once, in `Agent.execute()`, for every
    produce that reads it. For a produce that also declares `reacts_to`,
    this is a real guarantee, not best-effort: `Agent.execute()` also *skips
    calling it* if `context.get(event.artifact_id)` no longer resolves for a
    CREATED/UPDATED/STALE event (the artifact was deleted by another agent
    earlier in the same generation — the same race `Trigger.matches()`
    documents) — so `call.trigger` is never `None` when such a produce
    actually runs, and the body needs no guard at all. This guarantee does
    not apply to a DELETED event on the produce's own `reacts_to` type:
    there, `context.get(...)` correctly returning `None` *is* the event, not
    a race, so `call.trigger` is `None` and the produce still runs —
    deletion-reacting code is expected to handle that itself (`call.event`
    still carries `artifact_id`/`artifact_type` there). Without `reacts_to`,
    `call.trigger` is still resolved on a best-effort basis whenever
    `call.event` is not `None`, but never a reason to skip the call — there
    is no per-type contract to enforce.
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
    #: As a class attribute, always a tuple (or `None`), same convention as
    #: `also_creates` — `reacts_to = (Foo,)`, even for one type. The
    #: constructor parameter below additionally accepts a single bare type
    #: for convenience at a `Produce(Foo, reacts_to=Bar)` call site; either
    #: way it's normalized to this tuple-or-`None` shape by `__init__`, so
    #: external readers (`Agent._resolve_produce_call`) get one predictable
    #: shape to check `in` against.
    reacts_to: tuple[ArtifactType, ...] | None = None

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        *,
        also_creates: Sequence[ArtifactType] | None = None,
        reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
    ):
        self.artifact_type = artifact_type or self.__class__.artifact_type
        if self.artifact_type is None:
            raise ValueError(
                "artifact_type must be provided either as class attribute or constructor argument"
            )
        self.also_creates = (
            tuple(also_creates)
            if also_creates is not None
            else self.__class__.also_creates
        )
        resolved_reacts_to = (
            reacts_to if reacts_to is not None else self.__class__.reacts_to
        )
        # Normalizes the constructor argument's wider "single type or
        # sequence" shape down to `reacts_to`'s own plain tuple-or-`None`
        # shape — a bare `Produce(Foo, reacts_to=Bar)` call site is
        # convenient; the class attribute (set by `self.__class__.reacts_to`
        # above when no constructor argument was given) is always already a
        # tuple, same convention as `also_creates`.
        if resolved_reacts_to is None:
            self.reacts_to = None
        elif isinstance(resolved_reacts_to, tuple):
            self.reacts_to = resolved_reacts_to
        else:
            self.reacts_to = (cast(ArtifactType, resolved_reacts_to),)

    @property
    def effects(self) -> Effects:
        """The produce-scoped effect slot (authoring surface, §24).

        Only meaningful *inside* `produce()`: the runtime pushes a fresh slot
        per execution. Returns an error outside a run. Equivalent to (and
        backed by the same contextvar as) `call.effects` on the `ProduceCall`
        the current `produce()` call received — kept here too so a
        class-style produce can write `self.effects.create(...)` without
        threading `call` through every helper method.
        """
        from .effects import current_effects

        slot = current_effects()
        if slot is None:
            raise RuntimeError(
                "Produce.effects is only available while the runtime executes "
                "this produce — write effects inside produce(), not before it."
            )
        return slot

    async def produce(self, call: ProduceCall) -> None:
        """No-op by default (§24).

        Subclass-style overrides write `self.effects.*` (or `call.effects.*`)
        and return None; a `None` return means "no work". A bare
        `Produce(Model)` (no override) is a valid, deliberate no-op — used
        e.g. to widen an agent's allowed `Create` types when the actual
        write happens via `self.effects.ask(...)` in another produce (see
        `examples/supervisor`).
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
    *,
    also_creates: Sequence[ArtifactType] | None = None,
    reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
) -> Callable[[Callable[[ProduceCall], Any]], Produce[Any]]:
    """Decorator to create a Produce from a function.

    The function must accept exactly one argument, a `ProduceCall` — see its
    own docstring for `.context`/`.inputs`/`.event`/`.trigger`/`.effects`:

    - ``def f(call): return Model(...)`` — return style: return a model / a
      list of models / a ``Patch`` / ``None``; the runtime writes them into
      the effect slot (create/append) as usual;
    - ``def f(call): call.effects.create(...)`` — effects style: write
      ``call.effects.create/update/link/ask/...`` and return ``None`` — the
      function behaves exactly like a class produce with ``self.effects``.

    ``also_creates``/``reacts_to`` are the same knobs a class-style
    ``Produce`` subclass has (see ``Produce``'s own docstring) — passed
    through to the underlying instance since a decorated function has no
    class body of its own to set them as attributes on.
    """

    def decorator(func: Callable[[ProduceCall], Any]) -> Produce[Any]:
        params = list(inspect.signature(func).parameters.values())
        if len(params) != 1:
            raise TypeError(
                f"Function {func.__name__} must accept exactly one argument: "
                "a ProduceCall"
            )

        class _FunctionProduce(Produce[Any]):
            async def produce(self, call: ProduceCall) -> None:
                result = func(call)
                if asyncio.iscoroutine(result):
                    result = await result
                self._apply_result(result)

        # Return an instance of the Produce class with the required artifact_type
        instance = _FunctionProduce(
            artifact_type=artifact_type, also_creates=also_creates, reacts_to=reacts_to
        )
        return instance

    return decorator
