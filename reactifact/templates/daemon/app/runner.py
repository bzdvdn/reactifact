"""One daemon tick: fresh Context, one agent, one committed report."""

from __future__ import annotations

from datetime import UTC, datetime

from reactifact import Budget, Pipeline, PipelineRunner, RunConfig

from app.config import Settings
from app.models import Report, Tick
from app.monitor import monitor_agent
from app.resources import build_resources


def build_budget(settings: Settings) -> Budget:
    return Budget(
        max_runs=settings.budget_max_runs,
        max_iterations=settings.budget_max_iterations,
        max_seconds=settings.budget_max_seconds,
        max_tool_calls=settings.budget_max_tool_calls,
        max_tokens=settings.budget_max_tokens,
        max_cost=settings.budget_max_cost,
    )


def build_pipeline(settings: Settings) -> Pipeline:
    return Pipeline(
        agents=[monitor_agent(timeout=settings.agent_timeout_seconds)],
        seed_type=Tick,
        answer_type=Report,
        name="daemon-monitor",
    )


def build_run_config(settings: Settings) -> RunConfig:
    return RunConfig(
        resources=build_resources(settings),
        budget=build_budget(settings),
        traces=settings.trace_path if settings.tracing_enabled else None,
    )


async def run_once(settings: Settings, sequence: int) -> Report | None:
    config = build_run_config(settings)
    resources = config.resources
    assert resources is not None
    try:
        result = await PipelineRunner(build_pipeline(settings), config=config).arun(
            Tick(sequence=sequence, at=datetime.now(UTC))
        )
    finally:
        await resources.aclose()
    report = result.latest(Report)
    return report.data if report is not None else None


__all__ = ["build_budget", "build_pipeline", "build_run_config", "run_once"]
