"""Small internal helpers shared by the conversation recipes."""

from __future__ import annotations

from typing import Any


def _windowed(items: list[Any], window: int | None) -> list[Any]:
    if window is None:
        return items
    if window < 0:
        raise ValueError("window must be >= 0 or None")
    return items[-window:] if window else []
