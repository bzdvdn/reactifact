"""Trace sampling: keep all failures, a deterministic slice of the rest."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from .models import RunTrace


def _in_rate(trace_id: str, rate: float) -> bool:
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(trace_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / 2**64
    return bucket < rate


@dataclass(frozen=True)
class SamplingPolicy:
    """Tail-sampling policy applied to the finished `RunTrace`.

    `always_on_error` keeps every non-completed run (failed/stopped);
    `always_if` adds app-specific predicates (slow, a branch, a tenant);
    the rest is kept by a deterministic hash of the run id, so the same run
    is consistently included or excluded.
    """

    rate: float = 1.0
    always_on_error: bool = True
    always_if: Callable[[RunTrace], bool] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate <= 1.0:
            raise ValueError("SamplingPolicy.rate must be between 0 and 1")

    def should_sample(self, trace: RunTrace) -> bool:
        if self.always_on_error and trace.outcome != "completed":
            return True
        if self.always_if is not None and self.always_if(trace):
            return True
        return _in_rate(trace.id, self.rate)


#: What `Tracer(sample=...)` accepts.
Sample = Callable[[RunTrace], bool] | SamplingPolicy | bool | None


def keep_trace(trace: RunTrace, sample: Sample) -> bool:
    """Apply a `Tracer.sample` value to a finished trace."""
    if sample is None or sample is True:
        return True
    if sample is False:
        return False
    if isinstance(sample, SamplingPolicy):
        return sample.should_sample(trace)
    return bool(sample(trace))


__all__ = ["Sample", "SamplingPolicy", "keep_trace"]
