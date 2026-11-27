"""An LLM-touching stage, tested for its honest fallback (§59): with the
model forced to fail, the assistant stage still answers from the approved
plan instead of crashing or stalling.

Uses `lab.fail_resource("llm", ...)` (the general-purpose mock-and-fail
primitive, `reactifact.testing.mock`) rather than a hand-rolled failing
`LLMProvider` subclass — the same pattern as `lab.fail(tool, ...)`, just for
a resource instead of a tool.
"""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import RepairFlow
from ..models import ChatReply, PlanStep, Project, UserMsg
from ._common import ModelStub, resources


@scenario("repair: assistant falls back to the plan when the model is down")
async def assistant_falls_back_when_model_is_down() -> None:
    lab = ScenarioLab(
        [RepairFlow], resources=lambda: resources(ModelStub("stub-model"))
    )
    lab.fail_resource("llm", ConnectionError("model unreachable"))

    plan = [PlanStep(name="Демонтаж", description="Снять старое покрытие стен")]
    project = Project(stage="assistant", plan=plan, approved=True)
    result = await lab.run(project, UserMsg(text="что делать дальше?"))

    reply = result.artifacts(ChatReply).exists()
    assert "Демонтаж" in reply.text  # the fallback reads straight off the plan
    result.errors.none()  # llm_reply/structured_llm swallow provider errors (§59)
