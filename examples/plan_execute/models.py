"""plan_execute demo: artifact and structured-output schemas."""

from __future__ import annotations

from pydantic import BaseModel


class Goal(BaseModel):
    text: str


class PlanStep(BaseModel):
    goal: str
    index: int
    instruction: str


class StepResult(BaseModel):
    goal: str
    index: int
    instruction: str
    output: str


class FinalAnswer(BaseModel):
    goal: str
    text: str


class PlanBody(BaseModel):
    steps: list[str]


class _Text(BaseModel):
    text: str
