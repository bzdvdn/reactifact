"""The @step function decorator."""

from __future__ import annotations

import types
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from ..artifacts import ArtifactType
from ..consume import Consume
from ..policy import RetryPolicy
from ..provenance import DERIVED_FROM, RelationName
from .call import StepCall
from .core import Step
from .resolve import invoke_step


def _return_types(annotation: Any) -> tuple[type[BaseModel], ...] | None:
    """Artifact types declared by a return annotation, or None if unclear."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _return_types(get_args(annotation)[0])
    if origin in (tuple, list):
        found: list[type[BaseModel]] = []
        for part in get_args(annotation):
            if part is Ellipsis:
                continue
            nested = _return_types(part)
            if nested is None:
                return None
            found.extend(nested)
        return tuple(dict.fromkeys(found)) or None
    if origin in (types.UnionType, Union):
        found = []
        for part in get_args(annotation):
            if part is type(None):
                continue
            nested = _return_types(part)
            if nested is None:
                return None
            found.extend(nested)
        return tuple(dict.fromkeys(found)) or None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    return None


def _infer_produces(func: Callable[..., Any]) -> tuple[type[BaseModel], ...] | None:
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 — forward refs: require explicit produces
        return None
    return _return_types(hints.get("return"))


def step(
    artifact_type: ArtifactType | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    consumes: Consume | ArtifactType | Sequence[Consume | ArtifactType] | None = None,
    timeout: float | None = None,
    provenance: RelationName | str | bool | None = None,
    auto_link: bool | str | None = None,
    priority: int | None = None,
    concurrency_limit: int | None = None,
    name: str | None = None,
    retry: RetryPolicy | None = None,
    once: bool | None = None,
) -> Callable[[Callable[..., Any]], Step[Any]]:
    """Decorator to create a Step from a function.

    The canonical spellings:

        @step(produces=(Answer,), consumes=(Question,))
        async def answer(call: StepCall) -> None: ...

        @step(produces=Answer, consumes=Question)
        def answer(call): return Answer(...)

    `artifact_type`/`also_creates`/`reacts_to` are the 0.1.x spellings, kept
    for compatibility (`artifact_type` is the same as a one-element
    `produces`).

    The function may take one `call: StepCall` (the 0.1.x shape) or a typed
    signature — parameter annotations are artifact types (`T`, `list[T]`,
    `Optional[T]`, `Artifact[T]`, `Latest[T]`), plus `Context`, `Effects`,
    `Event`, `inputs`, `trigger`. A single `call` keeps
    `.context`/`.inputs`/`.event`/`.trigger`/`.effects`:

    - ``def f(call): return Model(...)`` — return style: return a model / a
      list of models / a ``Patch`` / ``None``; the runtime writes them into
      the effect slot (create/append) as usual;
    - ``def f(call): call.effects.create(...)`` — effects style: write
      ``call.effects.create/update/link/ask/...`` and return ``None`` — the
      function behaves exactly like a class step with ``call.effects``.
    """

    inferred = produces is None and artifact_type is None
    resolved_produces = produces if produces is not None else artifact_type

    def decorator(func: Callable[..., Any]) -> Step[Any]:
        inferred_produces = _infer_produces(func) if inferred else None
        final_produces = resolved_produces or inferred_produces
        if provenance is not None and auto_link is not None:
            raise ValueError(
                "Pass either provenance= or the deprecated auto_link=, not both"
            )
        chosen_provenance = provenance if provenance is not None else auto_link
        if chosen_provenance is None and inferred_produces is not None:
            chosen_provenance = DERIVED_FROM
        if final_produces is None:
            raise TypeError(
                f"@step({func.__name__!r}) needs produces=... or a return "
                "annotation naming a pydantic model (or tuple/list of them)"
            )

        class _FunctionStep(Step[Any]):
            _func: Callable[..., Any]

            async def run(self, call: StepCall) -> Any:
                return await invoke_step(self, call)

        instance = _FunctionStep(
            produces=final_produces,
            consumes=consumes,
            timeout=timeout,
            provenance=chosen_provenance,
            priority=priority,
            concurrency_limit=concurrency_limit,
            name=name,
            retry=retry,
            once=once if once is not None else (inferred_produces is not None),
        )
        # Keep the function for `Step.name` (a readable trace/log label).
        instance._func = func
        if instance.consumes is None:
            instance.consumes = Step._derive_consumes(func)
            if instance.consumes is not None:
                instance.reacts_to = tuple(
                    consume.artifact_type
                    for consume in instance.consumes
                    if consume.artifact_type is not None
                )
        return instance

    return decorator
