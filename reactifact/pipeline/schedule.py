"""Execution schedules: reactive fixpoint vs staged barriers.

`Pipeline` uses `ReactiveSchedule`; `StagedPipeline` uses `StagedSchedule`.
`Runtime` accepts either through `schedule=` (or `RunConfig.schedule`), so
`PipelineRunner`, `Chat` and `Daemon` share the same semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from ..agent import Agent
from ..agent.agent import AgentSource, normalize_units

if TYPE_CHECKING:
    from ..runtime import Runtime


@dataclass(frozen=True, init=False)
class Stage:
    """One staged execution phase.

    Agents inside a stage are fully reactive; the next stage starts only after
    this one settles. `reopen=True` lets an event produced by a later stage
    re-enter this stage (bounded by the turn's generation budget).
    """

    name: str
    agents: tuple[Agent, ...]
    reopen: bool
    wake: WakeMode

    def __init__(
        self,
        name: str,
        agents: Sequence[Agent | AgentSource],
        reopen: bool = False,
        wake: WakeMode = "turn",
    ) -> None:
        if not name:
            raise ValueError("Stage.name must be a non-empty string")
        if wake not in ("turn", "context", "events"):
            raise ValueError("Stage.wake must be 'turn', 'context' or 'events'")
        resolved = normalize_units(agents=list(agents))
        if not resolved:
            raise ValueError(f"Stage {name!r} needs at least one agent")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "agents", tuple(resolved))
        object.__setattr__(self, "reopen", reopen)
        object.__setattr__(self, "wake", wake)


#: How a stage seeds its first generation.
#:
#: - ``"turn"`` — artifacts created or updated since the turn started (default);
#: - ``"context"`` — every matching artifact in the context, once per entry;
#: - ``"events"`` — only pending events, never a synthetic wake pass.
WakeMode = Literal["turn", "context", "events"]


@runtime_checkable
class Schedule(Protocol):
    """A turn runner used by `Runtime` after the turn budget is started."""

    async def run(self, runtime: Runtime, max_iterations: int) -> int: ...


@dataclass(frozen=True)
class ReactiveSchedule:
    """The default: run the runtime's fixpoint loop over every agent."""

    async def run(self, runtime: Runtime, max_iterations: int) -> int:
        from ..runtime.loop import run_generations

        return await run_generations(runtime, max_iterations)


@dataclass(frozen=True, init=False)
class StagedSchedule:
    """Run stages in order, each to quiescence, with optional re-entry."""

    stages: tuple[Stage, ...]

    def __init__(self, stages: Sequence[Stage]) -> None:
        resolved = tuple(stages)
        if not resolved:
            raise ValueError("StagedSchedule needs at least one Stage")
        names = [stage.name for stage in resolved]
        if len(names) != len(set(names)):
            raise ValueError("StagedSchedule stage names must be unique")
        object.__setattr__(self, "stages", resolved)

    async def run(self, runtime: Runtime, max_iterations: int) -> int:
        from ..runtime.loop import run_stages

        return await run_stages(runtime, self.stages, max_iterations)


__all__ = [
    "ReactiveSchedule",
    "Schedule",
    "Stage",
    "StagedSchedule",
    "WakeMode",
]
