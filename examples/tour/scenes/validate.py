"""Contract validation: catch a missing producer before running anything."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Consume, Step, validate_contracts


class Question(BaseModel):
    text: str


class Middle(BaseModel):
    text: str


class Final(BaseModel):
    text: str


class MiddleProducer(Step):
    reads = (Question,)
    produces = (Middle,)

    async def run(self, item: Question) -> Middle:
        return Middle(text=item.text)


class Finish(Step):
    reads = (Middle,)
    produces = (Final,)

    async def run(self, item: Middle) -> Final:
        return Final(text=item.text)


def run_validate_scene() -> dict[str, Any]:
    producer = Agent("producer", consumes=[Consume(Question)], steps=[MiddleProducer()])
    finisher = Agent("finisher", consumes=[Consume(Middle)], steps=[Finish()])

    good = validate_contracts(
        agents=[producer, finisher],
        seeds=[Question],
        terminal=[Final],
    )
    bad = validate_contracts(
        agents=[finisher],
        seeds=[Question],
        terminal=[Final],
    )
    return {
        "ok": good.ok,
        "warnings": len(good.warnings),
        "bad_codes": [issue.code for issue in bad.errors],
        "bad_render": bad.render(),
    }
