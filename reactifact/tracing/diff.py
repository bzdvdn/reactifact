"""Compare two finished traces (`reactifact trace --against`)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .models import RunTrace


def _steps(trace: RunTrace) -> Counter[str]:
    out: Counter[str] = Counter()
    for span in trace.spans:
        for step in span.produces:
            out[step.name] += 1
    return out


def _spans(trace: RunTrace) -> Counter[str]:
    return Counter(span.agent for span in trace.spans)


def _calls(trace: RunTrace) -> Counter[str]:
    return Counter(
        f"{call.step or call.agent}:{call.model or call.provider or '-'}"
        for call in trace.llm_calls
    )


def _writes(trace: RunTrace) -> set[tuple[str, str]]:
    return {
        (write.data_type, write.artifact_id)
        for span in trace.spans
        for write in span.writes
    }


def _delta(left: Counter[str], right: Counter[str]) -> tuple[list[str], list[str]]:
    removed = sorted((left - right).elements())
    added = sorted((right - left).elements())
    return added, removed


@dataclass(frozen=True)
class TraceDiff:
    """A structural diff of two runs: outcome, cost, spans, steps, writes."""

    left: RunTrace
    right: RunTrace

    @property
    def token_delta(self) -> int:
        def tokens(trace: RunTrace) -> int:
            return sum(
                call.prompt_tokens + call.completion_tokens for call in trace.llm_calls
            )

        return tokens(self.right) - tokens(self.left)

    def to_text(self) -> str:
        lines = [
            f"trace diff {self.left.id[:8]} -> {self.right.id[:8]}",
            f"  outcome   {self.left.outcome or '-'} -> {self.right.outcome or '-'}",
            f"  duration  {self.left.duration_ms:.1f} -> {self.right.duration_ms:.1f} ms"
            f" ({self.right.duration_ms - self.left.duration_ms:+.1f})",
            f"  tokens    {self.token_delta:+d}",
        ]
        for label, left, right in (
            ("spans", _spans(self.left), _spans(self.right)),
            ("steps", _steps(self.left), _steps(self.right)),
            ("llm calls", _calls(self.left), _calls(self.right)),
        ):
            added, removed = _delta(left, right)
            if added:
                lines.append(f"  {label} + {', '.join(added)}")
            if removed:
                lines.append(f"  {label} - {', '.join(removed)}")
        left_writes, right_writes = _writes(self.left), _writes(self.right)
        for label, values in (
            ("writes +", sorted(right_writes - left_writes)),
            ("writes -", sorted(left_writes - right_writes)),
        ):
            for data_type, artifact_id in values:
                lines.append(f"  {label} {data_type} {artifact_id}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        return f"```text\n{self.to_text()}\n```"

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left.id,
            "right": self.right.id,
            "outcome": [self.left.outcome, self.right.outcome],
            "duration_ms": [self.left.duration_ms, self.right.duration_ms],
            "token_delta": self.token_delta,
        }


def diff_traces(left: RunTrace, right: RunTrace) -> TraceDiff:
    """Compare `left` (base) with `right` (candidate)."""
    return TraceDiff(left, right)


__all__ = ["TraceDiff", "diff_traces"]
