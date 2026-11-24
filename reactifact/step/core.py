"""The Step base class and its input-contract derivation."""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import (
    Annotated,
    Any,
    Generic,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel
from typing_extensions import TypeVar

from ..artifacts import Artifact, ArtifactType
from ..consume import Consume
from ..context import Context
from ..effects import Effects
from ..patches import Patch
from ..policy import RetryPolicy
from .annotations import Latest, Scope, Wakes, When
from .call import (
    current_step_call,
    current_step_context,
)

TOut = TypeVar("TOut", bound=BaseModel, default=BaseModel)


def _scope_value(scope: Scope | str | None, trigger: Artifact[Any] | None) -> Any:
    """The correlation key carried by `trigger` (owner-typed artifacts use id)."""
    if scope is None or trigger is None:
        return None
    if isinstance(scope, str):
        return getattr(trigger.data, scope, None)
    if scope.owner is not None and isinstance(trigger.data, scope.owner):
        return trigger.id
    if scope.key is not None:
        return scope.key(trigger.data)
    return getattr(trigger.data, scope.field, None)


def _scope_matches(
    scope: Scope | str | None, value: Any, artifact: Artifact[Any]
) -> bool:
    """Whether `artifact` belongs to the scope `value`."""
    if scope is None or value is None:
        return True
    if isinstance(scope, Scope) and scope.owner is not None:
        if isinstance(artifact.data, scope.owner):
            return bool(artifact.id == value)
        return bool(getattr(artifact.data, scope.field, None) == value)
    field = scope if isinstance(scope, str) else scope.field
    return bool(getattr(artifact.data, field, None) == value)


@dataclass(frozen=True)
class Emit:
    """A return-style instruction: create `data` with an explicit id and links.

    `@step` functions can return a model, but then the runtime picks the id.
    Return `Emit(Model(...), id="answer:42")` when the id must be stable, and
    optionally attach provenance in the same expression:

        return Emit(Answer(text=...), id=f"answer:{q.id}",
                    links=(("supported_by", evidence),))
    """

    data: Any
    id: str | None = None
    links: tuple[tuple[str, Any], ...] = ()


class Step(Generic[TOut]):
    """Describes the produced artifact type and how it is created.

    `artifact_type` is auto-derived from the generic when a subclass is written
    as `class X(Step[Foo])` — write it explicitly only to override or when
    the class has no generic (e.g. programmatic `Step(Foo)`).

    A step that creates more than one artifact type declares all of them in
    `produces`: `produces = (Primary, Extra)` (or the same tuple in the
    constructor / `@step`). `Runtime._validate_patch_types` checks every
    `Create` against the union of `produces` across the group's steps.

    `consumes` does double duty: it gates which events run the step and feeds
    its typed inputs. `Consume(..., wakes=False)` reads an input without
    waking on it. `call.trigger` is the artifact behind the current event —
    guaranteed live for CREATED/UPDATED/STALE events when `consumes` is
    declared; for a DELETED event it is `None` (use `call.event`), which is
    the event, not a race.
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
    consumes: tuple[Any, ...] | None = None
    #: Correlation scope for same-run lookups, e.g. `scope = "query_id"` or
    #: `scope = Scope("question_id", owner=Question)`. When set, typed inputs
    #: and `related()` are restricted to artifacts in the trigger's scope.
    scope: Scope | str | None = None
    #: Primary-output alias, kept for `Step[Foo]`/`@step(Foo)` and 0.1.x code.
    artifact_type: ArtifactType | None = None
    #: Hard per-step deadline, in seconds. The runtime cancels this step when
    #: it passes; the agent then fails (and `StepGroup.retry`, if set, may retry
    #: the whole agent). `StepGroup.timeout` remains the outer bound.
    timeout: float | None = None
    #: Scheduling priority within a generation (lower runs earlier), copied to
    #: the implicit group that `Runtime(steps=...)` creates.
    priority: int = 0
    #: Parallel executions of this step, copied to the implicit group.
    concurrency_limit: int | None = None
    #: Opt-in provenance default: when truthy, every artifact this step creates
    #: is linked to the inputs it actually resolved, `created —relation→ input`.
    #: `True` uses ``derived_from``; a string overrides the relation name.
    #: `False` (the default for now) preserves the explicit-`link` style.
    auto_link: bool | str = False
    #: Retry policy for this step, copied to the implicit group created by
    #: `Runtime(steps=...)`. `StepGroup` remains for shared retry across steps.
    retry: RetryPolicy | None = None

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
        if isinstance(value, (Consume, type)) or get_origin(value) is Annotated:
            items: Sequence[Any] = (value,)
        else:
            items = value
        return tuple(_as_consume(item) for item in items)

    @staticmethod
    def _derive_consumes(target: Callable[..., Any]) -> tuple[Consume, ...] | None:
        """Derive consumes from a typed run()/function signature."""
        try:
            hints = get_type_hints(target, include_extras=True)
            signature = inspect.signature(target)
        except Exception:  # noqa: BLE001 — unresolved annotations -> no derivation
            return None
        skip = {
            "self",
            "call",
            "step_call",
            "context",
            "effects",
            "event",
            "trigger",
            "inputs",
        }
        specs: list[Consume] = []
        for name, param in signature.parameters.items():
            if name in skip:
                continue
            annotation = hints.get(name, param.annotation)
            when = None
            wakes = None
            if get_origin(annotation) is Annotated:
                args = get_args(annotation)
                annotation = args[0]
                when = next((meta for meta in args[1:] if isinstance(meta, When)), None)
                wakes = next(
                    (meta for meta in args[1:] if isinstance(meta, Wakes)), None
                )
            if get_origin(annotation) is Latest:
                continue
            types_, _, _ = _annotation_types(annotation)
            for artifact_type in types_:
                specs.append(
                    Consume(
                        artifact_type,
                        condition=when,
                        wakes=wakes.value if wakes is not None else None,
                    )
                )
        return tuple(specs) or None

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        *,
        produces: ArtifactType | Sequence[ArtifactType] | None = None,
        consumes: Consume
        | ArtifactType
        | Sequence[Consume | ArtifactType]
        | None = None,
        timeout: float | None = None,
        auto_link: bool | str | None = None,
        priority: int | None = None,
        concurrency_limit: int | None = None,
        name: str | None = None,
        retry: RetryPolicy | None = None,
    ):
        resolved_produces = self._normalize_types(produces)
        if resolved_produces is None:
            resolved_produces = self._normalize_types(
                getattr(self.__class__, "produces", None)
            )
        legacy_type = artifact_type or self.__class__.artifact_type
        if resolved_produces is None and legacy_type is not None:
            resolved_produces = (legacy_type,)
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
        if resolved_consumes is None and type(self).run is not Step.run:
            resolved_consumes = self._derive_consumes(type(self).run)
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
        self.timeout = (
            timeout if timeout is not None else getattr(self.__class__, "timeout", None)
        )
        if self.timeout is not None:
            if not isinstance(self.timeout, (int, float)):
                raise TypeError("Step.timeout must be a number or None")
            if self.timeout <= 0:
                raise ValueError("Step.timeout must be > 0")
        self.auto_link = (
            auto_link
            if auto_link is not None
            else getattr(self.__class__, "auto_link", False)
        )
        if not isinstance(self.auto_link, (bool, str)):
            raise TypeError("Step.auto_link must be a bool or a relation name")
        self.priority = (
            priority if priority is not None else getattr(self.__class__, "priority", 0)
        )
        if not isinstance(self.priority, int):
            raise TypeError("Step.priority must be an int")
        self.concurrency_limit = (
            concurrency_limit
            if concurrency_limit is not None
            else getattr(self.__class__, "concurrency_limit", None)
        )
        if self.concurrency_limit is not None and self.concurrency_limit <= 0:
            raise ValueError("Step.concurrency_limit must be > 0")
        if name is not None:
            self.name = name
        self.retry = (
            retry if retry is not None else getattr(self.__class__, "retry", None)
        )
        if self.retry is not None and not isinstance(self.retry, RetryPolicy):
            raise TypeError("Step.retry must be a RetryPolicy or None")

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
        if self.scope is None:
            return None
        return _scope_value(self.scope, call.trigger)

    def related_all(self, artifact_type: ArtifactType) -> list[Artifact[Any]]:
        """Artifacts of a type from the current run's scope."""
        artifacts = self.context.list_artifacts(artifact_type)
        if self.scope is None:
            return artifacts
        value = self.scope_value
        if value is None:
            return artifacts
        return [
            artifact
            for artifact in artifacts
            if _scope_matches(self.scope, value, artifact)
        ]

    def related(self, artifact_type: ArtifactType) -> Artifact[Any] | None:
        """The latest same-scope artifact of a type, or `None`."""
        artifacts = self.related_all(artifact_type)
        return artifacts[-1] if artifacts else None

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Do this step's work. Override this in new code.

        Declare typed inputs and return the output(s):

            async def run(self, question: Question) -> Answer: ...

        The `call: StepCall` shape still works for steps that need the raw call.
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

    def _apply_result(self, result: Any, effects: Effects) -> None:
        """Writes a `@step`-decorated function's return-style result into
        the effect slot (§24).

        `None` — nothing; a model or a list of models — creates; a Patch — a
        legacy escape (its operations are appended to the effects).
        """
        if result is None:
            return
        if isinstance(result, Emit):
            handle = effects.create(result.data, id=result.id)
            for relation, target in result.links:
                handle.link(relation, target)
            return
        if isinstance(result, Patch):
            for op in result.operations:
                effects.add(op)
            return
        if isinstance(result, list):
            for item in result:
                self._apply_result(item, effects)
        else:
            effects.create(result)


def _as_consume(item: Any) -> Consume:
    """Normalize type/Consume/Annotated[T, When(...), Wakes(...)] into Consume."""
    origin = get_origin(item)
    if origin is Annotated:
        args = get_args(item)
        base = args[0]
        when = next((meta for meta in args[1:] if isinstance(meta, When)), None)
        wakes = next((meta for meta in args[1:] if isinstance(meta, Wakes)), None)
        return Consume(
            base,
            condition=when,
            wakes=wakes.value if wakes is not None else None,
        )
    if isinstance(item, Consume):
        return item
    return Consume(item)


def _annotation_types(annotation: Any) -> tuple[list[type], bool, bool]:
    """(artifact types, is_list, optional) for a run() parameter annotation."""
    optional = False
    if annotation is inspect.Parameter.empty:
        return [], False, False
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_types(get_args(annotation)[0])
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
