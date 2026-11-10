"""Typed input resolution for Step.run()/@step functions."""

from __future__ import annotations

import inspect
from collections.abc import Callable
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


def _resolve_arg(
    name: str,
    annotation: Any,
    call: StepCall,
    scope: Scope | str | None = None,
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
            return _matches(inner_types)
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
        if _trigger_matches(inner_types):
            assert call.trigger is not None
            return call.trigger
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
    if _trigger_matches(types_):
        assert call.trigger is not None
        return call.trigger.data
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
    if scope_value is None:
        scope_value = _scope_value(scope, call.trigger)
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
