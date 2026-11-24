"""Swarm: peer-to-peer handoffs with a hop limit and explicit provenance (§24).

A handoff is an artifact, not a function call: an agent creates a `Handoff`
naming the next agent, the helper runs that agent from the handoff, and the
chain is recorded in the returned Context. A cycle or a hop limit fails loudly
instead of spinning forever:

    result = await swarm(
        context,
        agents={"triage": triage, "billing": billing},
        start="triage",
        input=Question(text="refund please"),
        final_type=Answer,
        max_hops=6,
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..artifacts import Artifact
from ..budget import RunStats
from ..context import Context
from ..step import StepGroup
from ..subflow import run_subflow


class Handoff(BaseModel):
    """A peer-to-peer transfer: run `to`, with this payload and reason."""

    from_agent: str = ""
    to: str
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    hop: int = 0


class SwarmError(RuntimeError):
    """Base error for a broken swarm handoff."""


class SwarmCycle(SwarmError):
    """A handoff would revisit an agent (and `allow_repeat=False`)."""


class SwarmLimit(SwarmError):
    """The swarm reached `max_hops`."""


@dataclass
class SwarmResult:
    context: Context
    final: Artifact[Any] | None
    handoffs: list[Artifact[Handoff]] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    stats: list[RunStats] = field(default_factory=list)


def _handoff_of(outputs: Sequence[Artifact[Any]]) -> Artifact[Handoff] | None:
    for artifact in outputs:
        if isinstance(artifact.data, Handoff):
            return artifact
    return None


def _final_of(
    outputs: Sequence[Artifact[Any]], final_type: type[BaseModel] | None
) -> Artifact[Any] | None:
    if final_type is not None:
        for artifact in outputs:
            if isinstance(artifact.data, final_type):
                return artifact
        return None
    return outputs[-1] if outputs else None


async def swarm(
    parent: Context,
    groups: Mapping[str, StepGroup] | None = None,
    *,
    agents: Mapping[str, StepGroup] | None = None,
    start: str,
    input: BaseModel | Artifact[Any],
    final_type: type[BaseModel] | None = None,
    max_hops: int = 8,
    allow_repeat: bool = False,
    key: str = "swarm",
    name: str = "swarm",
) -> SwarmResult:
    """Runs a peer-to-peer handoff chain until an agent produces a final artifact.

    Each hop runs the target agent in a `run_subflow` (isolated child, parent
    tracer/budget), copies the `Handoff`/final artifact into a shared work
    Context, links it to the previous step, and continues. `max_hops` is a hard
    cap; `allow_repeat=False` also rejects revisiting an agent.
    """
    if groups is not None and agents is not None:
        raise ValueError("Pass either agents= (deprecated) or groups=, not both")
    mapping = groups if groups is not None else agents
    if mapping is None:
        raise TypeError("swarm requires groups=")
    if start not in mapping:
        raise SwarmError(f"unknown start agent {start!r}")
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")

    work = Context(resources=parent.resources)
    if isinstance(input, Artifact):
        seed = work.create(input.data, id=input.id)
    else:
        seed = work.create(input)

    current_input: Artifact[Any] = seed
    current = start
    handoffs: list[Artifact[Handoff]] = []
    run_ids: list[str] = []
    stats: list[RunStats] = []
    visited = [start]

    while True:
        if current not in mapping:
            raise SwarmError(f"unknown handoff target {current!r}")

        result = await run_subflow(
            work,
            groups=[mapping[current]],
            input=current_input,
            output_type=None,
            isolate=True,
            name=f"{name}:{current}",
        )
        run_ids.append(result.run_id)
        if result.stats is not None:
            stats.append(result.stats)
        if not result.outputs:
            raise SwarmError(f"agent {current!r} produced no output")

        handoff_out = _handoff_of(result.outputs)
        if handoff_out is not None:
            data = handoff_out.data
            if data.to in visited and not allow_repeat:
                raise SwarmCycle(
                    f"handoff from {current!r} to {data.to!r} would revisit an agent"
                )
            if len(handoffs) >= max_hops:
                raise SwarmLimit(f"swarm reached max_hops={max_hops} at {current!r}")
            hop = len(handoffs) + 1
            if not data.from_agent:
                data = data.model_copy(update={"from_agent": current})
            data = data.model_copy(update={"hop": hop})
            recorded = work.create(
                data,
                id=f"handoff:{key}:{hop}",
            )
            work.link(recorded.id, "handoff_from", current_input.id)
            handoffs.append(recorded)
            visited.append(data.to)
            current = data.to
            current_input = recorded
            continue

        final_out = _final_of(result.outputs, final_type)
        if final_out is None:
            expected = final_type.__name__ if final_type is not None else "an output"
            raise SwarmError(f"agent {current!r} produced no {expected}")
        final = work.create(
            final_out.data,
            id=f"swarm-final:{key}",
        )
        work.link(final.id, "derived_from", current_input.id)
        return SwarmResult(
            context=work,
            final=final,
            handoffs=handoffs,
            run_ids=run_ids,
            stats=stats,
        )


__all__ = [
    "Handoff",
    "SwarmCycle",
    "SwarmError",
    "SwarmLimit",
    "SwarmResult",
    "swarm",
]
