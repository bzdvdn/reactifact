"""Starter-app tools."""

from __future__ import annotations

from reactifact.tools import tool


@tool
async def add(a: float, b: float) -> str:
    """Add two numbers and return the sum."""
    return str(a + b)


__all__ = ["add"]
