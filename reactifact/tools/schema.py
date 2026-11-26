"""Tool argument-schema derivation from a function signature."""

from __future__ import annotations

import dataclasses
import inspect
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin, get_type_hints
from uuid import UUID

from pydantic import BaseModel, create_model

from ..di import Inject

#: Annotations that are data, never an injected collaborator. Everything else
#: that is a plain class (a client, a protocol implementation) is resolved from
#: `RuntimeResources` and excluded from the JSON schema.
_DATA_ANNOTATIONS = (
    str,
    int,
    float,
    bool,
    bytes,
    bytearray,
    type(None),
    dict,
    list,
    tuple,
    set,
    frozenset,
    datetime,
    date,
    datetime_time,
    timedelta,
    Decimal,
    UUID,
    Path,
)


def _resolved_annotations(fn: Any) -> dict[str, Any]:
    """Runtime annotations, resolving `from __future__ import annotations` strings."""
    try:
        return get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 — a forward ref may still be unresolvable
        return {}


def _annotation_kind(annotation: Any) -> Any:
    """Unwraps `Annotated[...]` so injection/data classification sees the real type."""
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0] if args else Any
    return annotation


def _is_injected(annotation: Any) -> bool:
    """Whether a tool parameter is a collaborator from `RuntimeResources`, not an argument.

    Plain classes (clients, protocols, services) are injected; pydantic models,
    dataclasses, enums and scalars/containers stay in the JSON schema.
    """
    if get_origin(annotation) is Annotated and any(
        isinstance(meta, Inject) for meta in get_args(annotation)[1:]
    ):
        return True
    annotation = _annotation_kind(annotation)
    if annotation is Any or annotation is inspect.Parameter.empty:
        return False
    if not isinstance(annotation, type):
        return False
    if issubclass(annotation, BaseModel) or issubclass(annotation, Enum):
        return False
    if dataclasses.is_dataclass(annotation):
        return False
    return not issubclass(annotation, _DATA_ANNOTATIONS)


def _args_model(fn: Any, injected: dict[str, Any] | None = None) -> type[BaseModel]:
    """Builds a Pydantic model of arguments from the function signature (→ JSON schema).

    Parameters that are injected collaborators are recorded in `injected` and
    omitted from the schema, so the model is never asked for them.
    """
    hints = _resolved_annotations(fn)
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any
        if _is_injected(annotation) or isinstance(param.default, Inject):
            if injected is not None:
                injected[name] = _annotation_kind(annotation)
            continue
        if param.default is inspect.Parameter.empty:
            fields[name] = (annotation, ...)  # required argument
        else:
            fields[name] = (annotation, param.default)
    return create_model(f"args_{fn.__name__}", **fields)


__all__ = ["_args_model", "_is_injected"]
