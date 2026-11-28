"""Agents used by the CLI `graph --check` test."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Agent, Step


class In(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class Make(Step):
    reads = (In,)
    produces = (Out,)

    async def run(self, item: In) -> Out:
        return Out(text=item.text)


GROUPS = [Agent("make", steps=[Make()])]
