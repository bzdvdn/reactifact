"""The daemon's one agent: turn a Tick into a Report."""

from __future__ import annotations

from reactifact import Artifact, Effects, Agent, Step
from reactifact.structured import structured_llm

from app.models import Report, ReportBody, Tick
from app.prompts import MONITOR_SYSTEM


class MonitorStep(Step):
    consumes = (Tick,)
    produces = (Report,)

    async def run(self, tick: Artifact[Tick], effects: Effects) -> None:
        if self.context.resources.llm is None:
            effects.create_once_from(
                tick,
                Report(
                    sequence=tick.data.sequence,
                    text=f"tick {tick.data.sequence}: no LLM configured",
                ),
                prefix="report",
            )
            return None
        body = await structured_llm(
            self.context.resources.llm,
            schema=ReportBody,
            system=MONITOR_SYSTEM,
            user=f"Tick {tick.data.sequence}. Write a status note.",
        )
        effects.create_once_from(
            tick,
            Report(
                sequence=tick.data.sequence,
                text=(
                    body.text
                    if body is not None
                    else f"tick {tick.data.sequence}: no report from LLM"
                ),
            ),
            prefix="report",
        )
        return None


def monitor_agent(*, timeout: float | None = None) -> Agent:
    """The daemon's agent, with an optional per-tick deadline."""
    return Agent("monitor", steps=[MonitorStep()], timeout=timeout)


#: The default agent (no deadline); `runner.py` builds one with the setting.
MonitorAgent = monitor_agent()


__all__ = ["MonitorAgent", "MonitorStep", "monitor_agent"]
