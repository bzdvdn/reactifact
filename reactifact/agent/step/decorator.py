"""The @step function decorator."""

from __future__ import annotations

import types
from collections.abc import Callable, Sequence
from typing import (
    Annotated,
    Any,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import BaseModel

from ...artifacts import ArtifactType
from ...consume import Consume
from ...policy import RetryPolicy
from ...provenance import DERIVED_FROM, RelationName
from ...read import Read
from .annotations import Scope
from .core import Step
from .guard import Guard
from .invocation import StepInvocation
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


@overload
def step(__func: Callable[..., Any], /) -> Step[Any]: ...


@overload
def step(
    artifact_type: ArtifactType | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    deletes: ArtifactType | Sequence[ArtifactType] | None = None,
    reads: Read
    | Consume
    | ArtifactType
    | Sequence[Read | Consume | ArtifactType]
    | None = None,
    guard: Guard | Sequence[Guard] | Callable[..., Any] | None = None,
    timeout: float | None = None,
    provenance: RelationName | str | bool | None = None,
    scope: Scope | str | None = None,
    priority: int | None = None,
    concurrency_limit: int | None = None,
    name: str | None = None,
    retry: RetryPolicy | None = None,
    once: bool | None = None,
) -> Callable[[Callable[..., Any]], Step[Any]]: ...


def step(
    artifact_type: ArtifactType | Callable[..., Any] | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    deletes: ArtifactType | Sequence[ArtifactType] | None = None,
    reads: Read
    | Consume
    | ArtifactType
    | Sequence[Read | Consume | ArtifactType]
    | None = None,
    guard: Guard | Sequence[Guard] | Callable[..., Any] | None = None,
    timeout: float | None = None,
    provenance: RelationName | str | bool | None = None,
    scope: Scope | str | None = None,
    priority: int | None = None,
    concurrency_limit: int | None = None,
    name: str | None = None,
    retry: RetryPolicy | None = None,
    once: bool | None = None,
) -> Step[Any] | Callable[[Callable[..., Any]], Step[Any]]:
    """Decorator to create a Step from a function.

    The canonical spellings:

        @step(produces=Answer, reads=Question)
        def answer(question: Question) -> Answer: ...

        @step(produces=(Answer,), reads=(Question,))
        async def answer(inv: StepInvocation) -> None: ...

    `artifact_type` is the 0.1.x spelling kept for compatibility (the same as a
    one-element `produces`).
    — put extra creates in `produces` and inputs in `reads`.

    The function may take a typed signature (the canonical form) or one
    low-level `inv: StepInvocation` (any annotated name) — parameter annotations are artifact types (`T`, `list[T]`,
    `Optional[T]`, `Artifact[T]`, `Latest[T]`), plus `Context`, `Effects`,
    `Event`, `inputs`, `trigger`. A single `call` keeps
    `.context`/`.inputs`/`.event`/`.trigger`/`.effects`:

    - ``def f(call): return Model(...)`` — return style: return a model / a
      list of models / a ``Patch`` / ``None``; the runtime writes them into
      the effect slot (create/append) as usual;
    - ``def f(call): call.effects.create(...)`` — effects style: write
      ``call.effects.create/update/delete/link/upsert/...`` and return ``None`` — the
      function behaves exactly like a class step with ``call.effects``.
    """

    if (
        artifact_type is not None
        and not isinstance(artifact_type, type)
        and callable(artifact_type)
    ):
        # Bare `@step`: infer `produces` from the return annotation.
        return step()(artifact_type)

    inferred = produces is None and artifact_type is None
    resolved_produces = (
        produces if produces is not None else cast(ArtifactType | None, artifact_type)
    )

    def decorator(func: Callable[..., Any]) -> Step[Any]:
        inferred_produces = _infer_produces(func) if inferred else None
        final_produces = (
            resolved_produces if resolved_produces is not None else inferred_produces
        )
        chosen_provenance = provenance
        if chosen_provenance is None and inferred_produces is not None:
            chosen_provenance = DERIVED_FROM
        if final_produces is None:
            raise TypeError(
                f"@step({func.__name__!r}) needs produces=... or a return "
                "annotation naming a pydantic model (or tuple/list of them)"
            )

        class _FunctionStep(Step[Any]):
            _func: Callable[..., Any]

            async def run(self, call: StepInvocation) -> Any:
                return await invoke_step(self, call)

        instance = _FunctionStep(
            produces=final_produces,
            deletes=deletes,
            reads=reads,
            guard=guard,
            timeout=timeout,
            provenance=chosen_provenance,
            scope=scope,
            priority=priority,
            concurrency_limit=concurrency_limit,
            name=name,
            retry=retry,
            once=once if once is not None else (inferred_produces is not None),
        )
        # Keep the function for `Step.name` (a readable trace/log label).
        instance._func = func
        existing = instance.wake_specs()
        if existing is None:
            resolved_consumes = Step._derive_consumes(func)
        else:
            resolved_consumes = Step._merge_read_only_consumes(existing, func)
        instance.reads = (
            tuple(Read.from_consume(consume) for consume in resolved_consumes)
            if resolved_consumes is not None
            else None
        )
        return instance

    return decorator
