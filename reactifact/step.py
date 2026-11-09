"""reactifact.step — how a produce writes an artifact (§24).

Two styles cover almost everything and are the two canonical ones — reach
for one of these first:

- **Subclass + effects** — `class X(Step[Model]): async def produce(self,
  call: StepCall) -> None: call.effects.create(...); return None`. Use
  this whenever the produce has its own state-free logic worth naming as a
  class (the common case in every example under `examples/`).
- **`@step(Model)` function** — `@step(Model)\\ndef f(call): call.effects
  .create(...)`, or the plain return-style `def f(call): return Model(...)`.
  One decorator, one argument shape either way — pick this for a short,
  one-off produce with no class ceremony.

Both styles take exactly one argument: a `StepCall`. Earlier versions
recognized a growing list of individually-named optional parameters
(`context`, `inputs`, `event`, `trigger`, `effects`) by inspecting the
function's signature — `StepCall` replaces that: one object, its fields
are what a produce can ever read (`.context`/`.inputs`/`.event`/`.trigger`)
plus the write-side `.effects`, all discoverable from an editor's
autocomplete on `call.` without chasing which combination of parameter names
does what. See `StepCall`'s own docstring for what each field is and when
it's populated.

One other thing `Step`/`Agent` accepts is *not* on that list on purpose:

- Overriding `Agent.run(self, event, context) -> Patch | None` directly,
  bypassing `Step` entirely (though not necessarily `effects` — the
  runtime still merges whatever `current_effects()` collected during the
  call, same as for a normal produce). This is a low-level, internal escape
  hatch for cases a `Step` genuinely can't express — not a third everyday
  style to reach for on a first pass. `reactifact.llm_agent.StructuredGenerateAgent`
  is the one built-in exception (writes via `current_effects()` directly
  instead of `self.effects`, since `Agent` — unlike `Step` — has no
  `effects` property); no example under `examples/` overrides `run()`.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Generic, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from typing_extensions import TypeVar

from .artifacts import Artifact, ArtifactType
from .consume import Consume
from .context import Context
from .effects import Effects
from .events import Event
from .patches import Patch

TOut = TypeVar("TOut", bound=BaseModel, default=BaseModel)


class Latest(Generic[TOut]):
    """Typed run() parameter marker: read the latest same-scope artifact of T.

    Unlike a plain `T` parameter, `Latest[T]` is resolved straight from the
    context (same `scope`) and does not need the type in `consumes` — it never
    triggers the step. It is the declarative replacement for
    `context.get(stage_id(...))` / `context.latest(T)` inside a step.
    """


#: The context of the step currently executing (for `self.context` on class
#: steps and `current_step_context()` in function steps).
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
      contextvar-backed slot as a class-style `Step`'s `self.effects`;
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
        from .effects import current_effects

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
        from .request import current_request

        return current_request()


class Step(Generic[TOut]):
    """Describes the produced artifact type and how it is created.

    `artifact_type` is auto-derived from the generic when a subclass is written
    as `class X(Step[Foo])` — write it explicitly only to override or when
    the class has no generic (e.g. programmatic `Step(Foo)`).

    A produce whose `step()` body legitimately creates more than one
    artifact type declares the extra ones in `also_creates` — either as a
    class attribute (`also_creates = (Bar, Baz)`) or a constructor argument
    (`Step(Foo, also_creates=[Bar])`). `Runtime._validate_patch_types`
    checks every `Create` op an agent's generation produces against the union
    of `artifact_type`/`also_creates` across that agent's `produces` list —
    without declaring `Bar` here, a `self.effects.create(Bar(...))` inside
    `artifact_type = Foo`'s own `step()` raises "not declared in produces"
    at commit time, since nothing recorded that this produce is allowed to
    write it. Before `also_creates` existed, the only way to widen that set
    was an inert second `Step(Bar)` placeholder added to the agent's
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
    `step()` at all for an event none of `reacts_to` matches, the same
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
    `StepCall.trigger` does it once, in `Agent.execute()`, for every
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
            if get_origin(base) is Step:
                args = get_args(base)
                if args and isinstance(args[0], type):
                    cls.artifact_type = args[0]
                    return

    #: Output contract: the artifact types this step may create. The first is
    #: the primary output (used for `.name`, provenance and traces); the rest
    #: are extra creates (what `also_creates` used to declare).
    produces: tuple[ArtifactType, ...] | None = None
    #: Input contract: the artifact types this step reads and reacts to. The
    #: runtime gates the step on these and collects them into `StepCall.inputs`.
    consumes: tuple[Consume | ArtifactType, ...] | None = None
    #: Correlation field for same-run lookups, e.g. `scope = "query_id"`.
    #: When set, typed inputs and `related()` are restricted to artifacts whose
    #: field equals the trigger's — one turn/session, not the whole context.
    scope: str | None = None
    #: Deprecated aliases, kept for 0.1.x code.
    artifact_type: ArtifactType | None = None
    also_creates: tuple[ArtifactType, ...] = ()
    reacts_to: tuple[ArtifactType, ...] | None = None

    @staticmethod
    def _normalize_types(
        value: ArtifactType | Sequence[ArtifactType] | None,
    ) -> tuple[ArtifactType, ...] | None:
        if value is None:
            return None
        if isinstance(value, type):
            return (value,)
        return tuple(value)

    @staticmethod
    def _normalize_consumes(
        value: Consume | ArtifactType | Sequence[Consume | ArtifactType] | None,
    ) -> tuple[Consume, ...] | None:
        if value is None:
            return None
        if isinstance(value, Consume):
            return (value,)
        if isinstance(value, type):
            return (Consume(value),)
        return tuple(
            item if isinstance(item, Consume) else Consume(item) for item in value
        )

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        *,
        produces: ArtifactType | Sequence[ArtifactType] | None = None,
        consumes: Consume
        | ArtifactType
        | Sequence[Consume | ArtifactType]
        | None = None,
        also_creates: Sequence[ArtifactType] | None = None,
        reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
    ):
        resolved_produces = self._normalize_types(produces)
        if resolved_produces is None:
            resolved_produces = self._normalize_types(
                getattr(self.__class__, "produces", None)
            )
        legacy_type = artifact_type or self.__class__.artifact_type
        legacy_extra = (
            tuple(also_creates)
            if also_creates is not None
            else self.__class__.also_creates
        )
        if resolved_produces is None and legacy_type is not None:
            resolved_produces = (legacy_type, *legacy_extra)
        elif resolved_produces is not None and legacy_extra:
            resolved_produces = resolved_produces + tuple(
                extra for extra in legacy_extra if extra not in resolved_produces
            )
        if resolved_produces is None:
            raise ValueError(
                "produces must be provided (a type, a sequence of types, or Step[Foo])"
            )
        self.produces = resolved_produces
        self.artifact_type = resolved_produces[0]
        self.also_creates = tuple(resolved_produces[1:])

        resolved_consumes = self._normalize_consumes(consumes)
        if resolved_consumes is None:
            resolved_consumes = self._normalize_consumes(
                getattr(self.__class__, "consumes", None)
            )
        if resolved_consumes is None:
            resolved_consumes = self._normalize_consumes(reacts_to)
        if resolved_consumes is None:
            resolved_consumes = self._normalize_consumes(
                getattr(self.__class__, "reacts_to", None)
            )
        self.consumes = resolved_consumes
        # Deprecated aliases: 0.1.x code reads `.reacts_to` (types, not specs).
        self.reacts_to = (
            tuple(
                consume.artifact_type
                for consume in resolved_consumes
                if consume.artifact_type is not None
            )
            if resolved_consumes is not None
            else None
        )

    def consume_specs(self) -> tuple[Consume, ...] | None:
        """The normalized (always-`Consume`) form of `consumes`."""
        return cast("tuple[Consume, ...] | None", self.consumes)

    def collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Artifacts this step consumes, in declaration order."""
        specs = self.consume_specs()
        if not specs:
            return []
        inputs: list[Artifact[Any]] = []
        for consume in specs:
            inputs.extend(consume.collect(context))
        return inputs

    @property
    def context(self) -> Context:
        """The `Context` of the current run (`current_step_context()`).

        Only meaningful inside `run()`; raises outside a runtime execution.
        """
        context = current_step_context()
        if context is None:
            raise RuntimeError(
                "Step.context is only available while the runtime executes "
                "this step — read it inside run(), not before it."
            )
        return context

    @property
    def cursor(self) -> dict[type, dict[str, int]]:
        """The input cursor of the current run (type → {artifact id → version})."""
        call = current_step_call()
        if call is None:
            return {}
        return {
            artifact_type: dict(vector) for artifact_type, vector in call.cursor.items()
        }

    @property
    def scope_value(self) -> Any:
        """The current run's correlation value (e.g. its `query_id`)."""
        call = current_step_call()
        if call is None:
            return None
        if call.scope is not None:
            return call.scope
        if call.trigger is None or not self.scope:
            return None
        return getattr(call.trigger.data, self.scope, None)

    def related_all(self, artifact_type: ArtifactType) -> list[Artifact[Any]]:
        """Artifacts of a type from the current run's scope."""
        artifacts = self.context.list_artifacts(artifact_type)
        if not self.scope:
            return artifacts
        value = self.scope_value
        if value is None:
            return artifacts
        return [
            artifact
            for artifact in artifacts
            if getattr(artifact.data, self.scope, None) == value
        ]

    def related(self, artifact_type: ArtifactType) -> Artifact[Any] | None:
        """The latest same-scope artifact of a type, or `None`."""
        artifacts = self.related_all(artifact_type)
        return artifacts[-1] if artifacts else None

    @property
    def effects(self) -> Effects:
        """The produce-scoped effect slot (authoring surface, §24).

        Only meaningful *inside* `step()`: the runtime pushes a fresh slot
        per execution. Returns an error outside a run. Equivalent to (and
        backed by the same contextvar as) `call.effects` on the `StepCall`
        the current `step()` call received — kept here too so a
        class-style produce can write `self.effects.create(...)` without
        threading `call` through every helper method.
        """
        from .effects import current_effects

        slot = current_effects()
        if slot is None:
            raise RuntimeError(
                "Step.effects is only available while the runtime executes "
                "this produce — write effects inside step(), not before it."
            )
        return slot

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Do this step's work. Override this in new code.

        New code declares typed inputs and returns its output(s):

            async def run(self, question: Question) -> Answer: ...

        The 0.1.x shape — a single `call: StepCall` — still works: the default
        `run` delegates to `produce(call)`, which old subclasses override.
        """
        if args and isinstance(args[0], StepCall):
            return await self.produce(args[0])
        return None

    async def produce(self, call: StepCall) -> None:
        """Deprecated 0.1.x hook; override `run` instead.

        Kept so existing `async def produce(self, call)` subclasses keep
        working: the default `run` delegates here.
        """
        return None

    #: Explicit display name (set by e.g. tool-use produces); when unset,
    #: `name` derives a readable one from the class/function/artifact type.
    _name: str | None = None

    @property
    def name(self) -> str:
        """A human-readable name for traces/logs.

        Subclass-style produces use their class name; an ``@step``-decorated
        function uses the wrapped function's name; a bare ``Step(Model)``
        (declared only to widen an agent's allowed ``Create`` types) falls back
        to its output artifact type — otherwise every such no-op would show up
        as a generic ``Step``. An explicit ``name`` (e.g. a tool-use produce's)
        always wins.
        """
        explicit = getattr(self, "_name", None)
        if explicit:
            return str(explicit)
        cls = type(self).__name__
        if cls not in ("Step", "_FunctionStep"):
            return cls
        func = getattr(self, "_func", None)
        if func is not None:
            return str(getattr(func, "__name__", cls))
        artifact = getattr(self.artifact_type, "__name__", "")
        return f"Step[{artifact}]" if artifact else cls

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

    def _apply_result(self, result: Any) -> None:
        """Writes a `@step`-decorated function's return-style result into
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


def _annotation_types(annotation: Any) -> tuple[list[type], bool, bool]:
    """(artifact types, is_list, optional) for a run() parameter annotation."""
    optional = False
    if annotation is inspect.Parameter.empty:
        return [], False, False
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        inner = args[0] if args else Any
        types_, _, _ = _annotation_types(inner)
        return types_, True, False
    if origin in (types.UnionType, Union):
        union_args = [a for a in get_args(annotation) if a is not type(None)]
        optional = len(union_args) != len(get_args(annotation))
        found: list[type] = []
        for arg in union_args:
            sub, _, _ = _annotation_types(arg)
            found.extend(sub)
        return found, False, optional or not union_args
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation], False, False
    return [], False, optional


def _resolved_hints(target: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except Exception:  # noqa: BLE001 — unresolved forward refs keep raw annotations
        return {}


def _resolve_arg(
    name: str,
    annotation: Any,
    call: StepCall,
    scope: str | None = None,
    scope_value: Any = None,
) -> Any:
    if name in {"call", "step_call"} or annotation is StepCall:
        return call
    if annotation is Context or name == "context":
        return call.context
    if annotation is Effects or name == "effects":
        return call.effects
    if annotation is Event or name == "event":
        return call.event
    if annotation is StepCall:
        return call
    if name == "trigger":
        return call.trigger
    if name == "inputs":
        return call.inputs

    def _matches(expected: list[type]) -> list[Artifact[Any]]:
        found: list[Artifact[Any]] = []
        seen: set[str] = set()
        for artifact in call.inputs:
            if type(artifact.data) not in expected or artifact.id in seen:
                continue
            if (
                scope is not None
                and scope_value is not None
                and getattr(artifact.data, scope, None) != scope_value
            ):
                continue
            found.append(artifact)
            seen.add(artifact.id)
        if (
            call.trigger is not None
            and type(call.trigger.data) in expected
            and call.trigger.id not in seen
            and (
                scope is None
                or scope_value is None
                or getattr(call.trigger.data, scope, None) == scope_value
            )
        ):
            found.append(call.trigger)
        return found

    origin = get_origin(annotation)
    if origin is Latest:
        args = get_args(annotation)
        inner_types, is_list, optional = _annotation_types(args[0] if args else Any)
        artifacts: list[Artifact[Any]] = (
            call.context.list_artifacts(inner_types[0]) if inner_types else []
        )
        if scope is not None and scope_value is not None:
            artifacts = [
                artifact
                for artifact in artifacts
                if getattr(artifact.data, scope, None) == scope_value
            ]
        if is_list:
            return [artifact.data for artifact in artifacts]
        if artifacts:
            return artifacts[-1].data
        if optional:
            return None
        return None
    if origin is Artifact:
        args = get_args(annotation)
        inner_types, is_list, optional = _annotation_types(args[0] if args else Any)
        found = _matches(inner_types)
        if is_list:
            return found
        if found:
            return found[-1]
        if optional:
            return None
        raise TypeError(
            f"Step parameter {name!r} expects Artifact[{inner_types}], "
            "but none is available in this run's inputs"
        )

    types_, is_list, optional = _annotation_types(annotation)
    if not types_:
        if optional:
            return None
        return call.trigger
    found = _matches(types_)
    if is_list:
        return [artifact.data for artifact in found]
    if found:
        return found[-1].data
    if optional:
        return None
    raise TypeError(
        f"Step parameter {name!r} expects {[t.__name__ for t in types_]}, "
        "but none is available in this run's inputs"
    )


async def _call_target(target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = target(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def invoke_step(step: Step[Any], call: StepCall) -> Any:
    """Calls a step's run/function with either a `StepCall` or typed inputs.

    A single `call: StepCall` parameter keeps the 0.1.x shape. Any other
    signature is Celery-style: parameter annotations are artifact types (or
    `list[T]`/`Optional[T]`/`T | None`), resolved from this run's inputs.
    """
    func = getattr(step, "_func", None)
    if func is None and type(step).run is Step.run:
        # 0.1.x class-style step: override `produce`, inherited `run`.
        return await step.run(call)
    target = func if func is not None else step.run
    signature = inspect.signature(target)
    params = list(signature.parameters.values())
    if func is None:
        params = [p for p in params if p.name != "self"]
    if not params:
        return await _call_target(cast(Any, target))
    if len(params) == 1 and (
        params[0].name in {"call", "step_call"} or params[0].annotation is StepCall
    ):
        return await _call_target(target, call)
    hints = _resolved_hints(target)
    scope = getattr(step, "scope", None)
    scope_value = call.scope
    if scope_value is None and scope is not None and call.trigger is not None:
        scope_value = getattr(call.trigger.data, scope, None)
    kwargs = {
        param.name: _resolve_arg(
            param.name,
            hints.get(param.name, param.annotation),
            call,
            scope,
            scope_value,
        )
        for param in params
    }
    return await _call_target(target, **kwargs)


def step(
    artifact_type: ArtifactType | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    consumes: Consume | ArtifactType | Sequence[Consume | ArtifactType] | None = None,
    also_creates: Sequence[ArtifactType] | None = None,
    reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
) -> Callable[[Callable[[StepCall], Any]], Step[Any]]:
    """Decorator to create a Step from a function.

    The canonical spellings:

        @step(produces=(Answer,), consumes=(Question,))
        async def answer(call: StepCall) -> None: ...

        @step(produces=Answer, consumes=Question)
        def answer(call): return Answer(...)

    `artifact_type`/`also_creates`/`reacts_to` are the 0.1.x spellings, kept
    for compatibility (`artifact_type` is the same as a one-element
    `produces`).

    The function must accept exactly one argument, a `StepCall` — see its
    own docstring for `.context`/`.inputs`/`.event`/`.trigger`/`.effects`:

    - ``def f(call): return Model(...)`` — return style: return a model / a
      list of models / a ``Patch`` / ``None``; the runtime writes them into
      the effect slot (create/append) as usual;
    - ``def f(call): call.effects.create(...)`` — effects style: write
      ``call.effects.create/update/link/ask/...`` and return ``None`` — the
      function behaves exactly like a class step with ``call.effects``.
    """

    resolved_produces = produces if produces is not None else artifact_type

    def decorator(func: Callable[[StepCall], Any]) -> Step[Any]:
        params = list(inspect.signature(func).parameters.values())
        if len(params) != 1:
            raise TypeError(
                f"Function {func.__name__} must accept exactly one argument: a StepCall"
            )

        class _FunctionStep(Step[Any]):
            _func: Callable[[StepCall], Any]

            async def run(self, call: StepCall) -> Any:
                return await invoke_step(self, call)

        instance = _FunctionStep(
            produces=resolved_produces,
            consumes=consumes,
            also_creates=also_creates,
            reacts_to=reacts_to,
        )
        # Keep the function for `Step.name` (a readable trace/log label).
        instance._func = func
        return instance

    return decorator
