"""The @step function decorator."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..artifacts import ArtifactType
from ..consume import Consume
from .call import StepCall
from .core import Step
from .resolve import invoke_step


def step(
    artifact_type: ArtifactType | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    consumes: Consume | ArtifactType | Sequence[Consume | ArtifactType] | None = None,
    also_creates: Sequence[ArtifactType] | None = None,
    reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
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

    resolved_produces = produces if produces is not None else artifact_type

    def decorator(func: Callable[..., Any]) -> Step[Any]:
        class _FunctionStep(Step[Any]):
            _func: Callable[..., Any]

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
