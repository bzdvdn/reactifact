"""Typed access to optional close hooks on collaborators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast


def aclose_of(value: object) -> Callable[[], Awaitable[None]] | None:
    """The object's async `aclose`, if it has one."""
    closer = getattr(value, "aclose", None)
    if not callable(closer):
        return None
    return cast(Callable[[], Awaitable[None]], closer)


def close_of(value: object) -> Callable[[], None] | None:
    """The object's sync `close`, if it has one."""
    closer = getattr(value, "close", None)
    if not callable(closer):
        return None
    return cast(Callable[[], None], closer)


__all__ = ["aclose_of", "close_of"]
