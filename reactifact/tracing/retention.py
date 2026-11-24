"""Trace retention: count, age, and protected runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RetentionPolicy:
    """How many / how old traces to keep, and what not to delete.

    `max_runs` bounds the number of *unprotected* runs; `max_age_days` drops
    older runs even inside that count. `keep_failed` protects every
    non-completed run; `keep_tags` protects runs carrying any listed tag.
    """

    max_runs: int | None = 200
    max_age_days: float | None = None
    keep_failed: bool = False
    keep_tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.max_runs is not None and self.max_runs < 0:
            raise ValueError("RetentionPolicy.max_runs must be >= 0 or None")
        if self.max_age_days is not None and self.max_age_days < 0:
            raise ValueError("RetentionPolicy.max_age_days must be >= 0 or None")

    def cutoff(self, *, now: datetime | None = None) -> datetime | None:
        if self.max_age_days is None:
            return None
        return (now or datetime.now(UTC)) - timedelta(days=self.max_age_days)

    def protects(self, *, outcome: str, tags: set[str]) -> bool:
        if self.keep_failed and outcome != "completed":
            return True
        return bool(self.keep_tags.intersection(tags))


__all__ = ["RetentionPolicy"]
