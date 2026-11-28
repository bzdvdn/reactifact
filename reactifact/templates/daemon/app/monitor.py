"""The daemon's one agent: turn a Tick into a Report."""

from __future__ import annotations

from reactifact import Agent, Artifact, Consume, Ensure, LLMProvider, step
from reactifact.structured import structured_llm

from app.models import Report, ReportBody, Tick
from app.prompts import MONITOR_SYSTEM


@step(reads=Tick, produces=Report)
async def monitor(tick: Artifact[Tick], llm: LLMProvider | None) -> Ensure:
    """One honest status note per tick (offline-safe)."""
    if llm is None:
        text = f"tick {tick.data.sequence}: no LLM configured"
    else:
        body = await structured_llm(
            llm,
            schema=ReportBody,
            system=MONITOR_SYSTEM,
            user=f"Tick {tick.data.sequence}. Write a status note.",
        )
        text = body.text if body is not None else f"tick {tick.data.sequence}: no report from LLM"
    return Ensure(
        Report(sequence=tick.data.sequence, text=text),
        source=tick,
        prefix="report",
    )


def monitor_agent(*, timeout: float | None = None) -> Agent:
    """The daemon's agent, with an optional per-tick deadline."""
    return Agent(
        "monitor",
        consumes=[Consume(Tick)],
        steps=[monitor],
        timeout=timeout,
    )


#: The default agent (no deadline); `runner.py` builds one with the setting.
MonitorAgent = monitor_agent()


__all__ = ["MonitorAgent", "monitor", "monitor_agent"]
