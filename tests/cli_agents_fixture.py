"""Agents used by the CLI `graph --check` test."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Step, create_step_group


class In(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class Make(Step):
    consumes = (In,)
    produces = (Out,)

    async def run(self, item: In) -> Out:
        return Out(text=item.text)


GROUPS = [create_step_group("make", steps=[Make()])]
