"""Golden-run helpers: freeze a run's fingerprint, assert a re-run matches.

A regression test on a real agent run wants two things pinned: the *state* it
produced (so a refactor that changes behavior fails) and the *prompts* it sent
(so a prompt edit that silently reworded a call is visible rather than buried in
a diff). `capture` records both — `reactifact.audit.context_hash`
plus every non-empty `LLMCall.prompt_hash` — and `assert_golden` re-checks them.

Typical use: record once against a real model with `ReplayLLM(mode="record")`
(or `ScenarioLab(mode="record")`), commit the JSONL, and in CI re-run under
`ReplayLLM(mode="replay")` with `replay_resources(recording)` then assert the
golden fingerprint — a deterministic, offline regression on the real run.

    golden = capture(context, trace=trace)            # after the record run
    # ... later, in CI:
    resources = replay_resources("calls.jsonl")
    context = await build(resources)                  # same agents, replaying LLM
    assert_golden(context, golden, trace=captured_trace)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audit import context_hash
from .exceptions import AssertionFailure

if TYPE_CHECKING:
    from ..context import Context
    from ..resources import RuntimeResources
    from ..tracing.models import RunTrace

#: Set to a truthy value (1/true/yes/on) to make `assert_golden_file` rewrite a
#: snapshot instead of comparing it — the "update snapshots" switch, like
#: syrupy/pytest-snapshot.
GOLDEN_UPDATE_ENV_VAR = "REACTIFACT_GOLDEN_UPDATE"


@dataclass(frozen=True)
class GoldenRun:
    """A run's frozen fingerprint: state hash + the prompt templates it used."""

    context_sha256: str
    prompt_hashes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "context_sha256": self.context_sha256,
            "prompt_hashes": list(self.prompt_hashes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenRun:
        return cls(
            context_sha256=data["context_sha256"],
            prompt_hashes=tuple(data.get("prompt_hashes", [])),
        )

    def save(self, path: str | Path) -> None:
        """Writes the fingerprint as JSON (parents created on demand)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> GoldenRun:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _update_requested(update: bool | None) -> bool:
    if update is not None:
        return update
    return os.environ.get(GOLDEN_UPDATE_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def prompt_hashes(trace: RunTrace) -> list[str]:
    """Every non-empty `LLMCall.prompt_hash` in a trace, in call order."""
    return [
        call.prompt_hash
        for span in trace.spans
        for call in span.llm_calls
        if call.prompt_hash
    ]


def capture(context: Context, *, trace: RunTrace | None = None) -> GoldenRun:
    """Freezes `context` (and optionally a trace) into a `GoldenRun`."""
    hashes = tuple(prompt_hashes(trace)) if trace is not None else ()
    return GoldenRun(context_sha256=context_hash(context), prompt_hashes=hashes)


def assert_golden(
    context: Context,
    golden: GoldenRun,
    *,
    trace: RunTrace | None = None,
) -> None:
    """Fails loudly when state (or, with `trace`, prompts) drifted."""
    actual = context_hash(context)
    if actual != golden.context_sha256:
        raise AssertionFailure(
            f"context hash drifted: expected {golden.context_sha256}, got {actual}"
        )
    if golden.prompt_hashes:
        if trace is None:
            raise AssertionFailure(
                "golden has prompt hashes but no trace was passed to compare"
            )
        actual_hashes = tuple(prompt_hashes(trace))
        if actual_hashes != golden.prompt_hashes:
            raise AssertionFailure(
                "prompt hashes drifted: "
                f"expected {golden.prompt_hashes}, got {actual_hashes}"
            )


def assert_golden_file(
    context: Context,
    path: str | Path,
    *,
    trace: RunTrace | None = None,
    update: bool | None = None,
) -> GoldenRun:
    """Path-aware snapshot assertion: writes the snapshot when it is missing
    (or `update`/`$REACTIFACT_GOLDEN_UPDATE` is set), otherwise compares it.

    The first run seeds the file, so a golden test is a one-liner and later
    drift fails. Returns the `GoldenRun` now on disk.
    """
    target = Path(path)
    if _update_requested(update) or not target.exists():
        golden = capture(context, trace=trace)
        golden.save(target)
        return golden
    golden = GoldenRun.load(target)
    assert_golden(context, golden, trace=trace)
    return golden


def replay_resources(
    recording: str | Path,
    *,
    base: RuntimeResources | None = None,
) -> RuntimeResources:
    """`base` (or a fresh `RuntimeResources`) whose llm replays `recording`.

    Drop-in for a re-run that must reproduce a recorded model: pair it with
    `capture`/`assert_golden` for an offline, deterministic regression on a
    real run.
    """
    from ..replay import ReplayLLM
    from ..resources import RuntimeResources

    resources = base if base is not None else RuntimeResources()
    resources.llm = ReplayLLM(recording, mode="replay")
    return resources


__all__ = [
    "GOLDEN_UPDATE_ENV_VAR",
    "GoldenRun",
    "assert_golden",
    "assert_golden_file",
    "capture",
    "prompt_hashes",
    "replay_resources",
]
