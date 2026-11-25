"""A tiny Pipeline + seed source for `reactifact daemon` tests."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Pipeline, step


class Tick(BaseModel):
    text: str


class Report(BaseModel):
    text: str


@step(consumes=Tick)
def report(tick: Tick) -> Report:
    return Report(text=tick.text.upper())


pipeline = Pipeline(steps=[report], seed_type=Tick, answer_type=Report)


def next_seed(sequence: int) -> Tick:
    return Tick(text=f"tick-{sequence}")
