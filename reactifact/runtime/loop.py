"""The turn fixpoint loop (generation → commit → next generation)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..budget import RunOutcome
from ..decisions import build_decision
from ..events import Event, EventType

if TYPE_CHECKING:
    from ..pipeline.schedule import Stage
    from .runtime import Runtime


def _stage_events(runtime: Runtime, stage: Stage) -> list[Event]:
    """Pending events that at least one agent in `stage` can consume."""
    return [
        event
        for event in runtime.context.pending_events()
        if any(
            agent.matching_triggers(event, runtime.context) for agent in stage.agents
        )
    ]


def _wake_events(runtime: Runtime, stage: Stage) -> list[Event]:
    """Synthetic events for already-existing artifacts a stage consumes.

    A stage boundary is an eligibility filter, not an event reset: an artifact
    produced earlier may have had its creation event consumed by an earlier
    stage. The wake pass lets this stage's agents see the current state once,
    while the normal pending-event queue still feeds newly produced artifacts.
    """
    pending = runtime.context.pending_events()
    pending_ids = {event.artifact_id for event in pending}
    events: list[Event] = []
    seen: set[tuple[str, type]] = set()
    for agent in stage.agents:
        specs = agent.consumes
        if specs is None:
            specs = [
                consume
                for step in agent.steps or ()
                for consume in (step.wake_specs() or ())
            ]
        for consume in specs:
            artifact_type = consume.artifact_type
            if artifact_type is None:
                continue
            if EventType.ARTIFACT_CREATED in consume.event_types:
                event_type = EventType.ARTIFACT_CREATED
            elif consume.event_types:
                event_type = consume.event_types[0]
            else:
                continue
            for artifact in runtime.context.list_artifacts(artifact_type):
                if artifact.id in pending_ids:
                    continue
                key = (artifact.id, type(artifact.data))
                if key in seen:
                    continue
                event = Event(event_type, type(artifact.data), artifact.id)
                if agent.matching_triggers(event, runtime.context):
                    seen.add(key)
                    events.append(event)
    return events


def _merge_events(*groups: list[Event]) -> list[Event]:
    seen: set[tuple[str, EventType]] = set()
    merged: list[Event] = []
    for group in groups:
        for event in group:
            key = (event.artifact_id, event.type)
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
    return merged


def _reopen_stage(
    runtime: Runtime, stages: tuple[Stage, ...], current_index: int
) -> int | None:
    """The earliest earlier stage that opted into re-entry, if any."""
    for index in range(current_index):
        stage = stages[index]
        if stage.reopen and _stage_events(runtime, stage):
            return index
    return None


async def _run_stage(runtime: Runtime, stage: Stage, limit: int) -> int:
    """Run one stage to quiescence, leaving later stages' events pending."""
    runtime._trace.decision(build_decision("stage_enter", detail={"stage": stage.name}))
    total = 0
    startup = _wake_events(runtime, stage)
    first = True
    while (
        runtime._generation < limit
        and not runtime._stop_requested.is_set()
        and not runtime._budget_exhausted()
    ):
        events = _stage_events(runtime, stage)
        if first:
            events = _merge_events(startup, events)
            first = False
        if not events:
            # No stage agent matches the pending events; outbox work is not
            # stage-scoped, so drain it once and let the next stage start.
            if runtime._outbox.has_pending_actions():
                total += await runtime._arun_once_impl(agents=stage.agents, events=[])
            break
        total += await runtime._arun_once_impl(agents=stage.agents, events=events)
    runtime._trace.decision(
        build_decision("stage_settled", detail={"stage": stage.name})
    )
    return total


async def run_stages(runtime: Runtime, stages: tuple[Stage, ...], limit: int) -> int:
    """Run a staged plan: each stage to quiescence, then advance.

    Events that match only a later stage stay in the context queue. Events a
    stage consumes are gone; the next stage wakes on the artifacts that stage
    produced (or on its own pending events). `Stage.reopen=True` lets a later
    event re-enter an earlier stage, bounded by the turn's generation limit.
    """
    total = 0
    index = 0
    while index < len(stages):
        if runtime._stop_requested.is_set():
            runtime.outcome = RunOutcome.STOPPED
            break
        if runtime._budget_exhausted() or runtime._generation >= limit:
            break
        total += await _run_stage(runtime, stages[index], limit)
        if runtime._stop_requested.is_set():
            runtime.outcome = RunOutcome.STOPPED
            break
        if runtime._budget_exhausted() or runtime._generation >= limit:
            break
        reopened = _reopen_stage(runtime, stages, index)
        if reopened is not None:
            index = reopened
            continue
        index += 1

    if (
        runtime.outcome == RunOutcome.COMPLETED
        and runtime._generation >= limit
        and runtime.context.pending_events()
    ):
        runtime.outcome = RunOutcome.ITERATIONS_EXHAUSTED
    return total


async def run_generations(runtime: Runtime, limit: int) -> int:
    """Run generations until quiescence, stop, budget exhaustion or `limit`."""
    total_runs = 0
    for _ in range(limit):
        if runtime._stop_requested.is_set():
            runtime.outcome = RunOutcome.STOPPED
            break
        if runtime._budget_exhausted():
            break
        runs = await runtime._arun_once_impl()
        total_runs += runs
        # Continue while the context is not quiescent: a generation that ran
        # no agent can still have left outbox work (or events emitted by the
        # drain) that the next generation must drain (§42).
        if runs == 0 and not runtime._has_pending_work():
            break
    else:
        if runtime.outcome == RunOutcome.COMPLETED:
            runtime.outcome = RunOutcome.ITERATIONS_EXHAUSTED
    return total_runs


__all__ = ["run_generations", "run_stages"]
