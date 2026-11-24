"""The fast-reply table answers a greeting without touching the LLM at all."""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..models import ChatReply, UserMsg
from ..step_groups import RepairFlow
from ._common import resources


@scenario("repair: greeting fast-path skips the model")
async def greeting_skips_the_model() -> None:
    lab = ScenarioLab([RepairFlow()], resources=resources)

    result = await lab.run(UserMsg(text="привет"))

    reply = result.artifacts(ChatReply).exists()
    assert "могу" in reply.text.lower() or "ремонт" in reply.text.lower()
    result.llm.max_calls(0)  # the fast-reply table answered, not the model
    result.errors.none()
