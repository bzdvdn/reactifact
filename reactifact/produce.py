"""reactifact.produce — deprecated 0.1.x compatibility layer for `reactifact.step`.

New code imports from `reactifact.step`:

    from reactifact import Step, StepCall, step

This module keeps the old names working for the 0.1.x surface:

- `Produce` is `Step`
- `ProduceCall` is `StepCall`
- `@produce(...)` is `@step(...)` (emits a `DeprecationWarning`)

The implementation lives in `reactifact.step`. This module exists only for the
deprecation window; it will be removed in 0.3.0.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from typing import Any

from .artifacts import ArtifactType
from .consume import Consume
from .step import Step, StepCall, step

#: Deprecated aliases for the canonical names in `reactifact.step`.
Produce = Step
ProduceCall = StepCall


def produce(
    artifact_type: ArtifactType | None = None,
    *,
    produces: ArtifactType | Sequence[ArtifactType] | None = None,
    consumes: Consume | ArtifactType | Sequence[Consume | ArtifactType] | None = None,
    also_creates: Sequence[ArtifactType] | None = None,
    reacts_to: ArtifactType | Sequence[ArtifactType] | None = None,
) -> Callable[[Callable[[StepCall], Any]], Step[Any]]:
    """Deprecated alias for `reactifact.step.step`."""
    warnings.warn(
        "reactifact.produce is deprecated; use reactifact.step (or import "
        "`step` from reactifact) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return step(
        artifact_type,
        produces=produces,
        consumes=consumes,
        also_creates=also_creates,
        reacts_to=reacts_to,
    )


__all__ = ["Produce", "ProduceCall", "produce"]
