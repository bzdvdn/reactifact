"""reactifact.patches — the `Patch` container (§12, §24).

`Patch` is the runtime's *transport*: an ordered set of `operations` (defined in
`reactifact.operations`) that gets applied as one commit. The authoring surface of
step is the `effects` slot; a `Patch` is built by the runtime (compiling the
effects slot), by `Effects.to_patch`, and by custom `Agent.run` implementations
that assemble a change-set by hand (the escape hatch).

The Operation types and `operation_from_dict` live in `reactifact.operations` and
are re-exported here for backward compatibility.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast

from .operations import (
    Create,
    Delete,
    Link,
    Operation,
    Relation,
    Unlink,
    Update,
    operation_from_dict,
)

if TYPE_CHECKING:
    from .artifacts import Artifact


def _id_of(value: Any) -> str:
    """Resolves an id from a string or an Artifact (or an effects handle)."""
    if isinstance(value, str):
        return value
    return cast(str, value.id)  # Artifact | effect Handle


def _auto_id(model_name: str) -> str:
    return f"{model_name.lower()}:{uuid.uuid4().hex[:8]}"


class Patch:
    """An ordered set of operations to apply to the Context (§12)."""

    def __init__(self, operations: list[Operation] | None = None):
        self.operations: list[Operation] = operations if operations is not None else []

    def add(self, op: Operation) -> Patch:
        self.operations.append(op)
        return self

    def create(self, data: Any, id: str | None = None) -> Patch:
        return self.add(Create(data, id=id))

    def update(self, artifact_id: Any, new_data: Any) -> Patch:
        return self.add(Update(_id_of(artifact_id), new_data))

    def update_fields(self, artifact: Artifact[Any], **fields: Any) -> Patch:
        """Update artifact fields without rebuilding the model.

        Sugar over `update`: it does the `model_copy(update=fields)` itself and puts
        the full new model into `Update` (preserving commit replayability).
        """
        return self.update(artifact.id, artifact.data.model_copy(update=fields))

    def delete(self, artifact_id: Any) -> Patch:
        return self.add(Delete(_id_of(artifact_id)))

    def link(self, artifact_id: Any, relation: str, target_id: Any) -> Patch:
        return self.add(Link(_id_of(artifact_id), relation, _id_of(target_id)))

    def unlink(
        self,
        artifact_id: Any,
        relation: str | None = None,
        target_id: Any | None = None,
    ) -> Patch:
        return self.add(
            Unlink(
                _id_of(artifact_id),
                relation,
                _id_of(target_id) if target_id is not None else None,
            )
        )

    def merge(self, *patches: Patch | None) -> Patch:
        """Adds operations from `patches` to this patch; None are skipped.

        Returns `self` (chaining, like `add`/`create`/`update`/`delete`).
        """
        for patch in patches:
            if patch is not None:
                self.operations.extend(patch.operations)
        return self

    def is_empty(self) -> bool:
        return len(self.operations) == 0


__all__ = [
    "Create",
    "Delete",
    "Link",
    "Operation",
    "Patch",
    "Relation",
    "Unlink",
    "Update",
    "operation_from_dict",
]
