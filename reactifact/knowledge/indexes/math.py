"""Shared vector math for index backends."""

from __future__ import annotations


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity in `[-1, 1]`; zero vectors score `0.0`."""
    dot: float = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm: float = sum(a * a for a in left) ** 0.5
    right_norm: float = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def clamp(value: float) -> float:
    """Clamp a similarity score into `[-1, 1]`."""
    return max(-1.0, min(1.0, value))


__all__ = ["clamp", "cosine_similarity"]
