"""Typed input resolution for Step.run()/@step functions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast, get_args, get_origin, get_type_hints

from ..artifacts import Artifact
from ..context import Context
from ..effects import Effects
from ..events import Event
from .annotations import Latest, Scope
from .call import StepCall
from .core import Step, _annotation_types, _scope_matches, _scope_value


def _resolved_hints(target: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(target)
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
        expected = ", ".join(self.expected)
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
                " If this step woke on an earlier trigger, declare "
                "Consume(<trigger type>, wakes=False) so it waits for the input."
            )
        else:
            message += (
                " No step in this runtime produces it: add a producer or seed "
                "the artifact before running."
            )
        return message


def _use(call: StepCall, *artifacts: Artifact[Any]) -> None:
    """Records artifacts actually resolved for this call (for `auto_link`)."""
    seen = {artifact.id for artifact in call.used}
    for artifact in artifacts:
        if artifact.id not in seen:
            call.used.append(artifact)
            seen.add(artifact.id)


def _resolve_arg(
    name: str,
    annotation: Any,
    call: StepCall,
    scope: Scope | str | None = None,
    scope_value: Any = None,
    *,
    step_name: str = "",
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
        if call.trigger is not None:
            _use(call, call.trigger)
        return call.trigger
    if name == "inputs":
        return call.inputs

    def _matches(expected: list[type]) -> list[Artifact[Any]]:
        found: list[Artifact[Any]] = []
        seen: set[str] = set()
        for artifact in call.inputs:
            if type(artifact.data) not in expected or artifact.id in seen:
                continue
            if not _scope_matches(scope, scope_value, artifact):
                continue
            found.append(artifact)
            seen.add(artifact.id)
        if (
            call.trigger is not None
            and type(call.trigger.data) in expected
            and call.trigger.id not in seen
            and _scope_matches(scope, scope_value, call.trigger)
        ):
            found.append(call.trigger)
        return found

    def _trigger_matches(expected: list[type]) -> bool:
        return (
            call.trigger is not None
            and type(call.trigger.data) in expected
            and _scope_matches(scope, scope_value, call.trigger)
        )

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        inner = args[0] if args else Any
        if get_origin(inner) is Artifact:
            inner_args = get_args(inner)
            inner_types, _, _ = _annotation_types(inner_args[0] if inner_args else Any)
            found = _matches(inner_types)
            _use(call, *found)
            return found
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
                if _scope_matches(scope, scope_value, artifact)
            ]
        if is_list:
            _use(call, *artifacts)
            return [artifact.data for artifact in artifacts]
        if artifacts:
            _use(call, artifacts[-1])
            return artifacts[-1].data
        if optional:
            return None
        return None
    if origin is Artifact:
        args = get_args(annotation)
        inner_types, is_list, optional = _annotation_types(args[0] if args else Any)
        found = _matches(inner_types)
        if is_list:
            _use(call, *found)
            return found
        if _trigger_matches(inner_types):
            assert call.trigger is not None
            _use(call, call.trigger)
            return call.trigger
        if found:
            _use(call, found[-1])
            return found[-1]
        if optional:
            return None
        available = {type(a.data).__name__ for a in call.context.list_artifacts()}
        raise MissingInputError(
            name,
            [type_.__name__ for type_ in inner_types],
            available,
            step_name=step_name,
        )

    types_, is_list, optional = _annotation_types(annotation)
    if not types_:
        if optional:
            return None
        return call.trigger
    found = _matches(types_)
    if is_list:
        _use(call, *found)
        return [artifact.data for artifact in found]
    if _trigger_matches(types_):
        assert call.trigger is not None
        _use(call, call.trigger)
        return call.trigger.data
    if found:
        _use(call, found[-1])
        return found[-1].data
    if optional:
        return None
    available = {type(a.data).__name__ for a in call.context.list_artifacts()}
    raise MissingInputError(
        name, [type_.__name__ for type_ in types_], available, step_name=step_name
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
    return await _call_target(target, **kwargs)
