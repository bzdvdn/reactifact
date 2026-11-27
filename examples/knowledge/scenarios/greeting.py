"""The router answers a greeting from its regex table without touching the
LLM."""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import AGENTS
from ..models import ChatReply, UserQuery
from ._common import resources


@scenario("knowledge: greeting fast-path skips the model")
async def greeting_skips_the_model() -> None:
    lab = ScenarioLab(list(AGENTS), resources=resources)

    result = await lab.run(UserQuery(text="hello"))

    reply = result.artifacts(ChatReply).exists()
    assert reply.kind == "greeting"
    result.llm.max_calls(0)
    result.errors.none()
