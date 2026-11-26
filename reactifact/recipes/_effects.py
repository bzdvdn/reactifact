"""Internal: the current produce's effect slot for recipe helpers.

These helpers (`fan_out_sources`, `materialize_doc`) are the low-level,
effect-slot based part of the recipes. They are not steps, so they cannot
return `Emit`/`Edit` instructions directly; the slot access lives here instead
of one recipe importing another recipe's private helper.
"""

from __future__ import annotations

from ..context import Context
from ..effects import Effects


def require_effects(context: Context, effects: Effects | None = None) -> Effects:
    """The explicit slot, or the current produce's `current_effects()`."""
    if effects is not None:
        return effects
    from ..effects import current_effects

    current = current_effects()
    if current is None:
        raise RuntimeError(
            "this recipe helper must run inside a produce "
            "(the runtime provides `effects` for the turn)"
        )
    return current


__all__ = ["require_effects"]
