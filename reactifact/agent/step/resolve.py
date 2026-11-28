"""Typed input resolution for Step.run()/@step functions."""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from ...artifacts import Artifact
from ...context import Context
from ...effects import Effects
from ...events import Event
from ...interaction import Human, Outbox, Progress
from ...invocation import Invocation, current_invocation
from ...request import CurrentRequest
from .annotations import Latest, Scope, Unscoped
from .core import Step, _annotation_types, _scope_matches, _scope_value
from .invocation import StepInvocation


def _resolved_hints(target: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(target, include_extras=True)
    except Exception:  # noqa: BLE001 — unresolved forward refs keep raw annotations
        return {}


class MissingInputError(TypeError):
    """A typed step parameter has no matching artifact in this run.

    `Runtime` enriches it with the groups that produce the missing type, so the
    message points at the wiring instead of just naming the parameter.
    """

    def __init__(
        self,
        parameter: str,
        expected: list[str],
        available: Iterable[str],
        *,
        step_name: str = "",
    ) -> None:
        self.parameter = parameter
        self.expected = expected
        self.available = sorted(set(available))
        self.step_name = step_name
        self.producers: dict[str, set[str]] = {}
        super().__init__(self._render())

    def annotate(self, producers: Mapping[str, Iterable[str]]) -> MissingInputError:
        """Adds producer names (called by the runtime, which knows all groups)."""
        wanted = set(self.expected)
        self.producers = {
            name: set(producers.get(name, ())) for name in wanted if producers.get(name)
        }
        self.args = (self._render(),)
        return self

    def _render(self) -> str:
        expected = ", ".join(self.expected) or "any artifact"
        step = f" in step {self.step_name!r}" if self.step_name else ""
        message = (
            f"Missing input{step}: parameter {self.parameter!r} expects {expected}, "
            "but no such artifact exists yet."
        )
        if self.available:
            shown = ", ".join(self.available[:12])
            more = "…" if len(self.available) > 12 else ""
            message += f" Available types: {shown}{more}."
        if self.producers:
            produced = "; ".join(
                f"{name} by {', '.join(sorted(groups))}"
                for name, groups in sorted(self.producers.items())
            )
            message += f" Produced by: {produced}."
            message += (
                " Typed parameters missing from an explicit consumes are "
                "already added as read-only inputs; seed or produce this type, "
                "or add Consume(T) if this step should also wait for it."
            )
        else:
            message += (
                " No step in this runtime produces it: add a producer or seed "
                "the artifact before running."
            )
        return message


def _use(call: StepInvocation, *artifacts: Artifact[Any]) -> None:
    """Records artifacts actually resolved for this call (for `provenance=`)."""
    seen = {artifact.id for artifact in call.used}
    for artifact in artifacts:
        if artifact.id not in seen:
            call.used.append(artifact)
            seen.add(artifact.id)


def _strip_annotated(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _is_unscoped(annotation: Any) -> bool:
    """Whether `Annotated[..., Unscoped()]` asks to ignore the step's scope."""
    if get_origin(annotation) is not Annotated:
        return False
    return any(isinstance(meta, Unscoped) for meta in get_args(annotation)[1:])


def _resolve_service(call: StepInvocation, annotation: Any) -> Any | None:
    """Resolve a non-artifact service parameter (`LLMProvider`, `X | None`, …).

    Explicit typed registrations win over the artifact heuristic: an app may
    register a pydantic settings object (`resources.register(Settings, ...)`)
    and inject it by type. Unregistered pydantic models stay data, so a
    consumer of `Project` keeps resolving the artifact. The rest is looked up
    in `RuntimeResources`; a union is unwrapped so `LLMProvider | None` injects
    the configured provider or honestly returns `None` — the same no-provider
    fallback the LLM helpers document.
    """
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        candidates = [
            _strip_annotated(arg)
            for arg in get_args(annotation)
            if arg is not type(None)
        ]
    else:
        candidates = [_strip_annotated(annotation)]
    for candidate in candidates:
        if not isinstance(candidate, type):
            continue
        registered = call.context.resources.get(candidate)
        if registered is not None:
            return registered
        if issubclass(candidate, BaseModel):
            continue
        if (service := call.context.resources.resolve(candidate)) is not None:
            return service
    return None


_MISSING = object()


def _untyped(annotation: Any) -> bool:
    return annotation is inspect.Parameter.empty or annotation is Any


def _handles_shape(annotation: Any) -> tuple[list[type], bool, bool] | None:
    """(types, is_list, optional) when the annotation yields artifact handles.

    `Artifact[T]`, `Artifact[T] | None`, `Annotated[Artifact[T], ...]` and
    `list[Artifact[T]]` qualify; the data forms (`T`, `T | None`, `list[T]`)
    do not.
    """
    annotation = _strip_annotated(annotation)
    origin = get_origin(annotation)
    if origin is Artifact:
        return _annotation_types(annotation)
    if origin is list:
        args = get_args(annotation)
        if args and get_origin(args[0]) is Artifact:
            return _annotation_types(annotation)
    if origin in (types.UnionType, Union):
        args = get_args(annotation)
        members = [arg for arg in args if arg is not type(None)]
        optional = len(members) != len(args)
        if len(members) == 1:
            inner = _handles_shape(members[0])
            if inner is not None:
                types_, is_list, inner_optional = inner
                return types_, is_list, optional or inner_optional
    return None


def _scoped_inputs(
    call: StepInvocation,
    types_: list[type],
    scope: Scope | str | None,
    scope_value: Any,
) -> list[Artifact[Any]]:
    found: list[Artifact[Any]] = []
    seen: set[str] = set()
    for artifact in call.inputs:
        if type(artifact.data) not in types_ or artifact.id in seen:
            continue
        if not _scope_matches(scope, scope_value, artifact):
            continue
        found.append(artifact)
        seen.add(artifact.id)
    return found


def _scoped_trigger(
    call: StepInvocation,
    types_: list[type],
    scope: Scope | str | None,
    scope_value: Any,
) -> Artifact[Any] | None:
    trigger = call.trigger
    if trigger is None or type(trigger.data) not in types_:
        return None
    if not _scope_matches(scope, scope_value, trigger):
        return None
    return trigger


def _with_trigger(
    artifacts: list[Artifact[Any]],
    trigger: Artifact[Any] | None,
) -> list[Artifact[Any]]:
    if trigger is None or any(item.id == trigger.id for item in artifacts):
        return artifacts
    return [*artifacts, trigger]


def _missing(
    call: StepInvocation,
    name: str,
    types_: list[type],
    step_name: str,
) -> MissingInputError:
    available = {type(a.data).__name__ for a in call.context.list_artifacts()}
    return MissingInputError(
        name,
        [type_.__name__ for type_ in types_],
        available,
        step_name=step_name,
    )


@dataclass(frozen=True)
class _Selection:
    """Artifacts matching one parameter's annotation in this call."""

    types: list[type]
    is_list: bool
    optional: bool
    trigger: Artifact[Any] | None
    artifacts: list[Artifact[Any]]


def _select(
    call: StepInvocation,
    types_: list[type],
    is_list: bool,
    optional: bool,
    scope: Scope | str | None,
    scope_value: Any,
) -> _Selection:
    return _Selection(
        types=types_,
        is_list=is_list,
        optional=optional,
        trigger=_scoped_trigger(call, types_, scope, scope_value),
        artifacts=_scoped_inputs(call, types_, scope, scope_value),
    )


def _resolve_selected(
    call: StepInvocation,
    name: str,
    selected: _Selection,
    step_name: str,
    *,
    handles: bool,
) -> Any:
    if not selected.types and not handles:
        return None if selected.optional else call.trigger
    if selected.is_list:
        artifacts = _with_trigger(selected.artifacts, selected.trigger)
        _use(call, *artifacts)
        return artifacts if handles else [artifact.data for artifact in artifacts]
    chosen = selected.trigger
    if chosen is None and selected.artifacts:
        chosen = selected.artifacts[-1]
    if chosen is not None:
        _use(call, chosen)
        return chosen if handles else chosen.data
    if selected.optional:
        return None
    raise _missing(call, name, selected.types, step_name)


def _resolve_latest(
    call: StepInvocation,
    annotation: Any,
    scope: Scope | str | None,
    scope_value: Any,
) -> Any:
    """Read a context query; a missing value is `None`, never an error."""
    args = get_args(annotation)
    inner = args[0] if args else Any
    handles = _handles_shape(inner)
    types_, is_list, _ = handles or _annotation_types(inner)
    artifacts = (
        [
            artifact
            for artifact in call.context.list_artifacts()
            if type(artifact.data) in types_
            and _scope_matches(scope, scope_value, artifact)
        ]
        if types_
        else []
    )
    if is_list:
        _use(call, *artifacts)
        return artifacts if handles is not None else [a.data for a in artifacts]
    if not artifacts:
        return None
    latest = artifacts[-1]
    _use(call, latest)
    return latest if handles is not None else latest.data


def _optional_base(annotation: Any) -> Any:
    """Unwrap `T | None` / `Optional[T]` to `T` when there is one member."""
    annotation = _strip_annotated(annotation)
    if get_origin(annotation) in (types.UnionType, Union):
        args = get_args(annotation)
        members = [_strip_annotated(arg) for arg in args if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _resolve_injected(name: str, annotation: Any, call: StepInvocation) -> Any:
    """Collaborators injected by type, or by name for untyped legacy steps."""
    base = _optional_base(annotation)
    untyped = _untyped(annotation) or _untyped(base)
    if base is StepInvocation or (untyped and name in {"call", "step_call"}):
        return call
    if base is Context or (untyped and name == "context"):
        return call.context
    if base is Effects or (untyped and name == "effects"):
        return call.effects
    if base is Human or (untyped and name == "human"):
        return Human(call.effects)
    if base is Outbox or (untyped and name == "outbox"):
        return Outbox(call.effects)
    if base is Progress or (untyped and name == "progress"):
        return Progress(call.context.announce)
    if base is CurrentRequest or (untyped and name == "request"):
        return call.request
    if base is Invocation or (untyped and name == "invocation"):
        return call.invocation or current_invocation()
    if base is Event or (untyped and name == "event"):
        return call.event
    # `trigger`/`inputs` are reserved pseudo-parameters: steps often annotate
    # them as `Artifact[Any] | None` / a list, so keep the name fallback.
    if name == "trigger":
        if call.trigger is not None:
            _use(call, call.trigger)
        return call.trigger
    if name == "inputs":
        return call.inputs
    return _MISSING


def _resolve_arg(
    name: str,
    annotation: Any,
    call: StepInvocation,
    scope: Scope | str | None = None,
    scope_value: Any = None,
    *,
    step_name: str = "",
) -> Any:
    """Resolve one parameter from collaborators, services or artifacts."""
    unscoped = _is_unscoped(annotation)
    annotation = _strip_annotated(annotation)
    injected = _resolve_injected(name, annotation, call)
    if injected is not _MISSING:
        return injected
    if (service := _resolve_service(call, annotation)) is not None:
        return service

    if unscoped:
        scope = None
        scope_value = None

    shape = _handles_shape(annotation)
    if shape is not None:
        selected = _select(call, *shape, scope, scope_value)
        return _resolve_selected(call, name, selected, step_name, handles=True)
    if get_origin(annotation) is Latest:
        return _resolve_latest(call, annotation, scope, scope_value)

    selected = _select(call, *_annotation_types(annotation), scope, scope_value)
    return _resolve_selected(call, name, selected, step_name, handles=False)


async def _call_target(target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = target(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def invoke_step(step: Step[Any], call: StepInvocation) -> Any:
    """Calls a step's run/function with either a `StepInvocation` or typed inputs.

    A single annotated `StepInvocation` parameter (any name) is the low-level
    form; unannotated `call`/`step_call` is the legacy path. Any other
    signature is Celery-style: parameters may be artifact data (`T`,
    `list[T]`, `T | None`), artifact handles (`Artifact[T]`,
    `list[Artifact[T]]`, `Artifact[T] | None`), `Latest[T]` context queries or
    collaborators (`Context`, `Effects`, `Progress`, `Human`, `Outbox`,
    `Event`, plus runtime services). `Annotated[...]` wrappers are unwrapped.
    `trigger` and `inputs` are reserved names: they always receive the event
    artifact and this call's input list.
    """
    func = getattr(step, "_func", None)
    if func is None and type(step).run is Step.run:
        # 0.1.x class-style step: override `produce`, inherited `run`.
        return await step.run(call)
    target = func if func is not None else step.run
    owner = getattr(step, "_owner", None)
    prefix: tuple[Any, ...] = (owner,) if owner is not None else ()
    signature = inspect.signature(target)
    params = list(signature.parameters.values())
    if owner is not None and params and params[0].name == "self":
        params = params[1:]
    if not params:
        return await _call_target(target, *prefix)
    if len(params) == 1 and (
        params[0].name in {"call", "step_call"}
        or params[0].annotation is StepInvocation
    ):
        return await _call_target(target, *prefix, call)
    hints = _resolved_hints(target)
    scope = getattr(step, "scope", None)
    scope_value = call.scope
    if scope_value is None:
        scope_value = _scope_value(scope, call.trigger)
    kwargs = {
        param.name: _resolve_arg(
            param.name,
            hints.get(param.name, param.annotation),
            call,
            scope,
            scope_value,
            step_name=step.name,
        )
        for param in params
    }
    return await _call_target(target, *prefix, **kwargs)
