"""reactifact.types — a stable identity for persisted artifact types.

Persistence stores each artifact's type as a string: `Artifact.data_type`, the
`data_type` of a compiled `Create`/`Update`, and `Event.artifact_type`. By
default that string is the Python qualified name (`module.qualname`) — which
breaks loading the moment a model is renamed or moved. This registry decouples
the persisted identity from Python's module layout:

    from reactifact.types import register

    register(
        Answer,
        type_id="answer",
        aliases=["old.module.Answer"],            # previously-persisted ids
        migrate=lambda d: {**d, "prose": d.pop("text", "")},  # field rename
    )

- `type_id_of(model)` — the string written on save: an explicit `type_id`
  (`register(...)` or a `TYPE_ID` classvar), else the qualified name (so
  nothing changes until you opt in).
- `resolve(type_id)` — the class on load: registry → alias → import by
  qualified name. Old payloads (bare qualified names) keep loading.
- `migrate_payload(type_id, raw)` — runs the registered `migrate(raw_dict)`
  before validation, so field renames / defaults survive a schema change.

The registry is process-global (like `reactifact.testing.registry`); register at
import time, next to the model.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

#: Transforms a persisted payload dict before it is validated into the model.
Migrate = Callable[[dict[str, Any]], dict[str, Any]]


class TypeNotFoundError(LookupError):
    """`resolve` could not find a class for a persisted type id."""


@dataclass
class TypeSpec:
    """One registered type: its stable id, aliases, and optional migration."""

    model: type[BaseModel]
    type_id: str
    aliases: tuple[str, ...] = ()
    migrate: Migrate | None = None


_SPECS: dict[type[BaseModel], TypeSpec] = {}
_BY_ID: dict[str, TypeSpec] = {}


def _qualname(model: type[Any]) -> str:
    return f"{model.__module__}.{model.__qualname__}"


def register(
    model: type[BaseModel],
    *,
    type_id: str | None = None,
    aliases: Iterable[str] = (),
    migrate: Migrate | None = None,
) -> type[BaseModel]:
    """Registers `model`'s stable `type_id` (default: its `TYPE_ID` classvar, or
    the qualified name), `aliases` (previously-persisted ids — e.g. a model's old
    qualified name after a move), and an optional `migrate` hook.

    Returns `model`, so it works as a decorator. Re-registering the same model
    replaces its spec; two *different* models claiming one id is an error.
    """
    resolved_id = type_id or getattr(model, "TYPE_ID", None) or _qualname(model)
    spec = TypeSpec(
        model=model,
        type_id=str(resolved_id),
        aliases=tuple(aliases),
        migrate=migrate,
    )
    _unregister(model)
    for key in (spec.type_id, *spec.aliases):
        existing = _BY_ID.get(key)
        if existing is not None and existing.model is not model:
            raise ValueError(
                f"type id {key!r} is already registered to {existing.model!r}; "
                f"cannot also map it to {model!r}"
            )
    _SPECS[model] = spec
    _BY_ID[spec.type_id] = spec
    for alias in spec.aliases:
        _BY_ID[alias] = spec
    return model


def _unregister(model: type[BaseModel]) -> None:
    old = _SPECS.pop(model, None)
    if old is None:
        return
    for key in (old.type_id, *old.aliases):
        if _BY_ID.get(key) is old:
            del _BY_ID[key]


def type_id_of(model: type[Any]) -> str:
    """The stable id written for `model` on save."""
    spec = _SPECS.get(model)
    if spec is not None:
        return spec.type_id
    explicit = getattr(model, "TYPE_ID", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return _qualname(model)


def resolve(type_id: str) -> type[BaseModel]:
    """The class for a persisted `type_id`, or raise `TypeNotFoundError`.

    Registry first, then a registered alias, then importing the id as a
    qualified name (so pre-registry payloads keep loading).
    """
    spec = _BY_ID.get(type_id)
    if spec is not None:
        return spec.model
    module_name, sep, class_name = type_id.rpartition(".")
    if sep:
        try:
            candidate = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError, ValueError):
            candidate = None
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    raise TypeNotFoundError(
        f"unknown artifact type {type_id!r}: no registered type id/alias resolves "
        f"it, and it is not importable as a qualified name. Register it with "
        f"reactifact.types.register(Model, type_id=..., aliases=[{type_id!r}]) to "
        f"keep old sessions/recordings loadable."
    )


def migrate_payload(type_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Applies the registered `migrate` hook for `type_id` (identity otherwise)."""
    spec = _BY_ID.get(type_id)
    if spec is not None and spec.migrate is not None:
        return spec.migrate(raw)
    return raw


def registered_types() -> list[TypeSpec]:
    """Every registered spec (introspection / diagnostics)."""
    return list(_SPECS.values())


__all__ = [
    "Migrate",
    "TypeNotFoundError",
    "TypeSpec",
    "migrate_payload",
    "register",
    "registered_types",
    "resolve",
    "type_id_of",
]
