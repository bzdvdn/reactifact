"""plan_execute demo: the single agent wiring planner, executor, and finisher."""

from __future__ import annotations

from reactifact import Agent, Consume

from .models import Goal, PlanStep, StepResult
from .produce import Executor, Finisher, Planner


class Flow(Agent):
    name = "plan_execute"
    consumes = [Consume(Goal), Consume(PlanStep), Consume(StepResult)]
    produces = [Planner(), Executor(), Finisher()]
