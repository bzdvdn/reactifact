"""One daemon tick: fresh Context, one agent, one committed report."""

from __future__ import annotations

from datetime import UTC, datetime

from reactifact import Budget, Context, Runtime
from reactifact.tracing import Tracer, TraceStore

from app.config import Settings
from app.models import Report, Tick
from app.monitor import MonitorAgent
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


async def run_once(settings: Settings, sequence: int) -> Report | None:
    resources = build_resources(settings)
    context = Context(resources=resources)
    context.create(Tick(sequence=sequence, at=datetime.now(UTC)))
    tracer = Tracer(store=TraceStore(settings.trace_path)) if settings.tracing_enabled else None
    runtime = Runtime(
        context,
        agents=[MonitorAgent(timeout=settings.agent_timeout_seconds)],
        budget=build_budget(settings),
        tracer=tracer,
    )
    try:
        await runtime.arun()
    finally:
        await resources.aclose()
    report = context.latest(Report)
    return report.data if report is not None else None


__all__ = ["build_budget", "run_once"]
