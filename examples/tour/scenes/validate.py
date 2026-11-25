"""Contract validation: catch a missing producer before running anything."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import Step, create_step_group, validate_agents


class Question(BaseModel):
    text: str


class Middle(BaseModel):
    text: str


class Final(BaseModel):
    text: str


class MiddleProducer(Step):
    consumes = (Question,)
    produces = (Middle,)

    async def run(self, item: Question) -> Middle:
        return Middle(text=item.text)


class Finish(Step):
    consumes = (Middle,)
    produces = (Final,)

    async def run(self, item: Middle) -> Final:
        return Final(text=item.text)


def run_validate_scene() -> dict[str, Any]:
    producer = create_step_group("producer", steps=[MiddleProducer()])
    finisher = create_step_group("finisher", steps=[Finish()])

    good = validate_agents(
        [producer, finisher],
        seeds=[Question],
        terminal=[Final],
    )
    bad = validate_agents(
        [finisher],
        seeds=[Question],
        terminal=[Final],
    )
    return {
        "ok": good.ok,
        "warnings": len(good.warnings),
        "bad_codes": [issue.code for issue in bad.errors],
        "bad_render": bad.render(),
    }
