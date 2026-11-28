"""The Step base class and its input-contract derivation."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
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

from ...artifacts import Artifact, ArtifactType
from ...consume import Consume
from ...context import Context
from ...effects import Effects
from ...patches import Patch
from ...policy import RetryPolicy
from ...provenance import DERIVED_FROM, RelationName
from ...read import Read
from .annotations import Latest, Scope, Wakes, When
from .call import (
    StepCall,
    current_step_call,
    current_step_context,
)

if TYPE_CHECKING:
    from ...interaction import Progress
    from ...request import CurrentRequest

TOut = TypeVar("TOut", bound=BaseModel, default=BaseModel)


def _normalize_provenance(
    value: RelationName | str | bool | None,
) -> RelationName | str | None:
    if value is None or value is False:
        return None
    if value is True:
        return DERIVED_FROM
    if isinstance(value, (RelationName, str)):
        return value
    raise TypeError("Step.provenance must be a RelationName/str, bool or None")


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

    With an id that already exists, `Emit` refreshes the artifact (a new
    version) — the return-style equivalent of `Effects.upsert`. Use `Ensure`
    when an existing artifact should be left untouched instead.
    """

    data: Any
    id: str | None = None
    links: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Ensure:
    """A return-style idempotent create: create only if the id is absent.

    `Ensure(Model(...), id="answer:42")` mirrors `Effects.create_once`;
    `Ensure(Model(...), source=question)` mirrors `Effects.create_once_from`
    (the id is `f"{prefix or type(data).__name__.lower()}:{source.id}"`).
    Links are applied only when the artifact is actually created; if it already
    exists, the instruction is a no-op (no new version, no links).
    """

    data: Any
    id: str | None = None
    source: Any = None
    prefix: str | None = None
    links: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if (self.id is None) == (self.source is None):
            raise ValueError("Ensure needs exactly one of id= or source=")


class Edit:
    """A return-style update: change fields of an existing artifact.

    `Edit(ticket, status="closed")` compiles to `effects.update(...)`, so a
    chain of `effects.update` calls is not needed for the common case.
    """

    __slots__ = ("artifact", "fields", "expected_version")

    def __init__(
        self,
        artifact: Artifact[Any] | str,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ):
        self.artifact = artifact
        self.expected_version = expected_version
        self.fields = fields


class Drop:
    """A return-style delete: remove an artifact."""

    __slots__ = ("artifact",)

    def __init__(self, artifact: Artifact[Any] | str):
        self.artifact = artifact


class Relate:
    """A return-style link: `source —relation→ target`."""

    __slots__ = ("source", "relation", "target")

    def __init__(self, source: Any, relation: str, target: Any):
        self.source = source
        self.relation = relation
        self.target = target


class Step(Generic[TOut]):
    """Describes the produced artifact type and how it is created.

    `artifact_type` is auto-derived from the generic when a subclass is written
    as `class X(Step[Foo])` — write it explicitly only to override or when
    the class has no generic (e.g. programmatic `Step(Foo)`).

    A step that creates more than one artifact type declares all of them in
    `produces`: `produces = (Primary, Extra)` (or the same tuple in the
    constructor / `@step`). `Runtime._validate_patch_types` checks every
    `Create` against the union of `produces` across the group's steps.

    `reads` is the step's input set: the runtime calls the step when those
    artifacts exist and feeds them as typed inputs. `Agent.consumes` decides
    when the agent wakes; a step never needs its own wake contract. `Consume`
    conditions and `wakes=False` still refine reads when needed. `call.trigger`
    is the artifact behind the current event — guaranteed live for
    CREATED/UPDATED/STALE events once `reads` is declared; for a DELETED event
    it is `None` (use `call.event`), which is the event, not a race.
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
    #: are extra creates (what `also_creates` used to declare). `()` is valid
    #: for a side-effect-only step that only deletes/links.
    produces: tuple[ArtifactType, ...] | None = None
    #: Declarative delete contract for side-effect-only steps. Contracts show
    #: it; the runtime does not gate deletes on it.
    deletes: tuple[ArtifactType, ...] = ()
    #: Input contract: the artifacts this step reads. The runtime gates the
    #: step on their availability and collects them into `StepCall.inputs`.
    reads: tuple[Any, ...] | None = None
    #: Correlation scope for same-run lookups, e.g. `scope = "query_id"` or
    #: `scope = Scope("question_id", owner=Question)`. When set, typed inputs
    #: and `related()` are restricted to artifacts in the trigger's scope.
    scope: Scope | str | None = None
    #: Primary-output alias, kept for `Step[Foo]`/`@step(Foo)` and 0.1.x code.
    artifact_type: ArtifactType | None = None
    #: Hard per-step deadline, in seconds. The runtime cancels this step when
    #: it passes; the agent then fails (and `Agent.retry`, if set, may retry
    #: the whole agent). `Agent.timeout` remains the outer bound.
    timeout: float | None = None
    #: Scheduling priority within a generation (lower runs earlier), copied to
    #: the implicit group that `Runtime(steps=...)` creates.
    priority: int = 0
    #: Parallel executions of this step, copied to the implicit group.
    concurrency_limit: int | None = None
    #: Declarative provenance: when set, every artifact this step creates is
    #: linked to the inputs it actually resolved, `created —relation→ input`.
    #: Use `reactifact.provenance` constants (`DERIVED_FROM`, `SUPPORTED_BY`,
    #: …); `True` means `derived_from`, `None`/`False` disables. Custom strings
    #: are allowed.
    provenance: RelationName | str | bool | None = None
    #: Retry policy for this step, copied to the implicit group created by
    #: `Runtime(steps=...)`. `Agent` remains for shared retry across steps.
    retry: RetryPolicy | None = None
    #: Stable id for return-style creates: when true, a created model gets
    #: `type:trigger:content-hash` instead of a random id, so a re-trigger
    #: refreshes the same artifact instead of duplicating it. The decorator
    #: turns this on when `produces` is inferred from the return annotation.
    once: bool = False
    #: Set by `bind()` for `@step`-decorated methods; the owner is passed as
    #: the first positional argument by `invoke_step`.
    _owner: Any = None

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
    def _normalize_reads(value: Any) -> tuple[Consume, ...] | None:
        """Normalize `Read`/type/`Annotated`/legacy `Consume` into Consume specs."""
        if value is None:
            return None
        if isinstance(value, (Read, Consume, type)) or get_origin(value) is Annotated:
            items: Sequence[Any] = (value,)
        else:
            items = value
        specs: list[Consume] = []
        for item in items:
            if isinstance(item, Read):
                specs.append(item.to_consume())
            else:
                specs.append(_as_consume(item))
        return tuple(specs)

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

    @staticmethod
    def _merge_read_only_consumes(
        existing: tuple[Consume, ...], target: Callable[..., Any]
    ) -> tuple[Consume, ...]:
        """Add typed artifact params missing from explicit `reads`.

        An explicit `reads` narrows the input set; the typed signature may name
        more inputs. A type in `run()`/the function but absent from `reads`
        becomes `Consume(T, wakes=False)` (preserving `When`), so reads need no
        `wakes=False` boilerplate. Types already covered by an
        explicit `Consume` are skipped — `Context.list_artifacts` is
        subclass-inclusive, so an explicit superclass already collects them.
        """
        derived = Step._derive_consumes(target)
        if not derived:
            return existing
        covered = tuple(
            consume.artifact_type
            for consume in existing
            if consume.artifact_type is not None
        )
        seen = set(covered)
        extras: list[Consume] = []
        for spec in derived:
            artifact_type = spec.artifact_type
            if artifact_type is None or artifact_type in seen:
                continue
            if any(issubclass(artifact_type, covered_type) for covered_type in covered):
                continue
            seen.add(artifact_type)
            extras.append(
                Consume(
                    artifact_type,
                    condition=spec.condition,
                    event_types=spec.event_types,
                    wakes=False,
                )
            )
        if not extras:
            return existing
        return (*existing, *extras)

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        *,
        produces: ArtifactType | Sequence[ArtifactType] | None = None,
        deletes: ArtifactType | Sequence[ArtifactType] | None = None,
        reads: (
            Read
            | Consume
            | ArtifactType
            | Sequence[Read | Consume | ArtifactType]
            | None
        ) = None,
        timeout: float | None = None,
        provenance: RelationName | str | bool | None = None,
        scope: Scope | str | None = None,
        priority: int | None = None,
        concurrency_limit: int | None = None,
        name: str | None = None,
        retry: RetryPolicy | None = None,
        once: bool | None = None,
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
                "produces must be provided (a type, a sequence of types, or "
                "Step[Foo]); use produces=() for a side-effect-only step"
            )
        self.produces = resolved_produces
        self.artifact_type = resolved_produces[0] if resolved_produces else None
        resolved_deletes = self._normalize_types(deletes)
        if resolved_deletes is None:
            resolved_deletes = self._normalize_types(
                getattr(self.__class__, "deletes", None)
            )
        self.deletes = resolved_deletes or ()

        resolved_consumes = self._normalize_reads(reads)
        if resolved_consumes is None:
            resolved_consumes = self._normalize_reads(
                getattr(self.__class__, "reads", None)
            )
        if resolved_consumes is None:
            if type(self).run is not Step.run:
                resolved_consumes = self._derive_consumes(type(self).run)
        elif type(self).run is not Step.run:
            # Explicit reads = the input set; the typed signature adds any
            # remaining typed params as read-only inputs.
            resolved_consumes = self._merge_read_only_consumes(
                resolved_consumes, type(self).run
            )
        self.reads = (
            tuple(Read.from_consume(consume) for consume in resolved_consumes)
            if resolved_consumes is not None
            else None
        )
        self.scope = (
            scope if scope is not None else getattr(self.__class__, "scope", None)
        )
        self.timeout = (
            timeout if timeout is not None else getattr(self.__class__, "timeout", None)
        )
        if self.timeout is not None:
            if not isinstance(self.timeout, (int, float)):
                raise TypeError("Step.timeout must be a number or None")
            if self.timeout <= 0:
                raise ValueError("Step.timeout must be > 0")
        raw_provenance = provenance
        if raw_provenance is None:
            raw_provenance = getattr(self.__class__, "provenance", None)
        self.provenance = _normalize_provenance(raw_provenance)
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
        self.once = once if once is not None else getattr(self.__class__, "once", False)
        if not isinstance(self.once, bool):
            raise TypeError("Step.once must be a bool")

    def read_specs(self) -> tuple[Consume, ...] | None:
        """The runtime `Consume` view of `reads`."""
        raw = (
            self.reads
            if self.reads is not None
            else getattr(self.__class__, "reads", None)
        )
        return self._normalize_reads(raw)

    def bind(self, owner: Any) -> Step[Any]:
        """Bind a `@step`-decorated method to its `Agent` instance.

        A method step is declared at class level, so its function still has an
        unbound `self`. The agent clones the step per instance and the runtime
        passes that owner as the first positional argument.
        """
        clone = copy.copy(self)
        if getattr(self, "_func", None) is not None:
            clone._owner = owner
        return cast("Step[Any]", clone)

    def collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Artifacts this step reads, in declaration order."""
        specs = self.read_specs()
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
    def request(self) -> CurrentRequest:
        """The active turn's per-request payload (`Runtime.arun(request=...)`).

        The same data as `StepCall.request` / `current_request()`, as a small
        read-only `CurrentRequest` mapping. Empty outside a request.
        """
        from ...request import CurrentRequest, current_request

        return CurrentRequest(current_request())

    @property
    def progress(self) -> Progress:
        """The `Progress` collaborator for the current run.

        A thin view over `Context.announce`; use it from a class step (or a
        custom `ToolUse` subclass) without adding a `progress` parameter:
        `self.progress("Planning…")`.
        """
        from ...interaction import Progress

        return Progress(self.context.announce)

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

    def _return_id(self, data: Any, call: StepCall | None) -> str | None:
        """A stable id for a return-style create, when `once` is on."""
        if not self.once or call is None or call.trigger is None:
            return None
        payload = json.dumps(
            data.model_dump(mode="json"), sort_keys=True, default=str
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:12]
        return f"{type(data).__name__.lower()}:{call.trigger.id}:{digest}"

    def _apply_result(
        self,
        result: Any,
        effects: Effects,
        call: StepCall | None = None,
    ) -> None:
        """Writes a `@step`-decorated function's return-style result into
        the effect slot (§24).

        `None` — nothing; a model, a list or a tuple of models — creates;
        a Patch — a legacy escape (its operations are appended to the effects).
        """
        if result is None:
            return
        if isinstance(result, Emit):
            handle = effects.create(result.data, id=result.id)
            for relation, target in result.links:
                handle.link(relation, target)
            return
        if isinstance(result, Ensure):
            artifact_id = result.id
            if artifact_id is None:
                source = result.source
                source_id = source if isinstance(source, str) else source.id
                artifact_id = (
                    f"{result.prefix or type(result.data).__name__.lower()}:{source_id}"
                )
            ensured = effects.create_once(result.data, id=artifact_id)
            if ensured is None:
                return
            for relation, target in result.links:
                ensured.link(relation, target)
            return
        if isinstance(result, Edit):
            target = (
                result.artifact
                if isinstance(result.artifact, Artifact)
                else effects.context.get(result.artifact)
            )
            if target is None:
                raise ValueError(f"Edit target {result.artifact!r} does not exist")
            effects.update(
                target,
                expected_version=result.expected_version,
                **result.fields,
            )
            return
        if isinstance(result, Drop):
            effects.delete(result.artifact)
            return
        if isinstance(result, Relate):
            effects.link(result.source, result.relation, result.target)
            return
        if isinstance(result, Patch):
            for op in result.operations:
                effects.add(op)
            return
        if isinstance(result, (list, tuple)):
            for item in result:
                self._apply_result(item, effects, call)
        else:
            effects.create(result, id=self._return_id(result, call))


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
    if origin is Artifact:
        args = get_args(annotation)
        return _annotation_types(args[0] if args else Any)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation], False, False
    return [], False, optional
