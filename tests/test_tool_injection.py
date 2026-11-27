"""Tool DI, per-tool retry/timeout and typed results."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Depends,
    Inject,
    RetryPolicy,
    Runtime,
    RuntimeResources,
    Step,
    StepCall,
    tool,
)
from reactifact.tools import ToolError, ToolOutput, execute_tool
from reactifact.tools.use import ToolAnswer, ToolUse


class Pod(BaseModel):
    name: str


class PodClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, name: str) -> str:
        self.calls.append(name)
        return f"pod/{name}"


@tool
async def get_pod(name: str, pods: PodClient) -> Pod:
    """Read one pod through the injected client."""
    return Pod(name=await pods.get(name))


def test_service_parameter_is_injected_and_not_in_schema():
    resources = RuntimeResources()
    client = resources.register(PodClient, PodClient())

    output = asyncio.run(execute_tool(get_pod, {"name": "web"}, resources=resources))

    assert output.data == {"name": "pod/web"}
    assert client.calls == ["web"]
    assert get_pod.schema["required"] == ["name"]
    assert "pods" not in get_pod.schema["properties"]


def test_injection_works_inside_a_step_without_explicit_resources():
    resources = RuntimeResources()
    client = resources.register(PodClient, PodClient())

    class Question(BaseModel):
        text: str

    class Answer(BaseModel):
        text: str

    class ToolStep(Step[Answer]):
        artifact_type = Answer
        consumes = [Consume(Question)]

        async def run(self, call: StepCall):
            output = await execute_tool(get_pod, {"name": call.trigger.data.text})
            call.effects.create(Answer(text=output.data["name"]))

    ctx = Context(resources=resources)
    ctx.create(Question(text="api"))
    asyncio.run(Runtime(ctx, steps=[ToolStep()]).arun())

    answers = ctx.list_artifacts(Answer)
    assert [a.data.text for a in answers] == ["pod/api"]
    assert client.calls == ["api"]


def test_missing_service_explains_how_to_register_it():
    resources = RuntimeResources()
    with pytest.raises(RuntimeError, match="PodClient"):
        asyncio.run(execute_tool(get_pod, {"name": "web"}, resources=resources))


def test_injection_outside_a_step_asks_for_resources():
    with pytest.raises(RuntimeError, match="not running inside a step"):
        asyncio.run(get_pod.execute({"name": "web"}))


def test_typed_pydantic_result_becomes_json_data():
    @tool
    async def make_pod(name: str) -> Pod:
        return Pod(name=name)

    output = asyncio.run(execute_tool(make_pod, {"name": "db"}))
    assert output.data == {"name": "db"}
    assert output.text == ""


def test_retry_on_exception_then_success():
    attempts = {"n": 0}

    @tool(retry=RetryPolicy(max_attempts=3, backoff=0))
    async def flaky(x: str) -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("temporary")
        return f"ok:{x}"

    output = asyncio.run(execute_tool(flaky, {"x": "a"}))
    assert output.text == "ok:a"
    assert attempts["n"] == 3


def test_retry_gives_up_after_max_attempts():
    attempts = {"n": 0}

    @tool(retry=RetryPolicy(max_attempts=2, backoff=0))
    async def always_fails(x: str) -> str:
        attempts["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(execute_tool(always_fails, {"x": "a"}))
    assert attempts["n"] == 2


def test_retryable_tool_error_is_retried_and_plain_error_is_not():
    attempts = {"n": 0}

    @tool(retry=RetryPolicy(max_attempts=3, backoff=0))
    async def busy(x: str) -> ToolOutput:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return ToolOutput.failed(
                ToolError(code="busy", message="busy", retryable=True)
            )
        return ToolOutput(text="done")

    assert asyncio.run(execute_tool(busy, {"x": "a"})).text == "done"
    assert attempts["n"] == 3

    attempts["n"] = 0

    @tool(retry=RetryPolicy(max_attempts=3, backoff=0))
    async def rejected(x: str) -> ToolOutput:
        attempts["n"] += 1
        return ToolOutput.failed(ToolError(code="denied", message="no access"))

    output = asyncio.run(execute_tool(rejected, {"x": "a"}))
    assert output.error == "no access"
    assert attempts["n"] == 1


def test_timeout_bounds_one_attempt_and_can_be_retried():
    attempts = {"n": 0}

    @tool(timeout=0.01, retry=RetryPolicy(max_attempts=2, backoff=0))
    async def slow(x: str) -> str:
        attempts["n"] += 1
        await asyncio.sleep(0.1)
        return "late"

    with pytest.raises(TimeoutError):
        asyncio.run(execute_tool(slow, {"x": "a"}))
    assert attempts["n"] == 2


def test_tool_use_passes_resources_to_injected_tools():
    """The LLM loop resolves a tool's service from the run's resources."""

    class LLM:
        def __init__(self) -> None:
            self.responses = [
                '{"type":"tool_call","tool":"get_pod","args":{"name":"web"}}',
                '{"type":"answer","text":"web is up"}',
            ]

        async def complete(self, request):
            from reactifact.providers import LLMResponse

            return LLMResponse(text=self.responses.pop(0))

        async def stream(self, request):
            from reactifact.providers import LLMResponse

            yield LLMResponse(text="")

    class Ask(BaseModel):
        text: str

    class Agent_(Agent):
        consumes = [Consume(Ask), Consume(ToolAnswer)]
        produces = [
            ToolUse(
                name="ops",
                system="Use the tools.",
                tools=[get_pod],
            )
        ]

    resources = RuntimeResources(llm=LLM())
    client = resources.register(PodClient, PodClient())
    ctx = Context(resources=resources)
    ctx.create(Ask(text="check web"))
    asyncio.run(Runtime(ctx, groups=[Agent_()]).arun())

    answer = ctx.list_artifacts(ToolAnswer)[0].data.text
    assert answer == "web is up"
    assert client.calls == ["web"]


@dataclass
class Settings:
    prefix: str


@tool
async def annotated_client(name: str, settings: Annotated[Settings, Inject()]) -> str:
    return f"{settings.prefix}:{name}"


def test_inject_marks_an_otherwise_ambiguous_collaborator():
    resources = RuntimeResources()
    resources.register(Settings, Settings("prod"))

    assert "settings" not in annotated_client.schema["properties"]
    output = asyncio.run(
        execute_tool(annotated_client, {"name": "web"}, resources=resources)
    )
    assert output.text == "prod:web"


_DEPENDS = Depends()


@tool
async def default_depends(name: str, settings: Settings = _DEPENDS) -> str:
    return f"{settings.prefix}:{name}"


def test_depends_can_be_used_as_a_default_marker():
    resources = RuntimeResources()
    resources.register(Settings, Settings("dev"))

    assert "settings" not in default_depends.schema["properties"]
    output = asyncio.run(
        execute_tool(default_depends, {"name": "api"}, resources=resources)
    )
    assert output.text == "dev:api"


def test_tool_use_surfaces_structured_tool_errors_to_the_model():
    loop = ToolUse(system="", tools=[])
    tool = ToolOutput.failed(
        ToolError(code="E_BAD", message="no access", retryable=False)
    )

    from reactifact.tools.base import Tool as BaseTool

    class Broken(BaseTool):
        name = "broken"

        async def execute(self, args):  # noqa: ANN001, ANN201 - test double
            return tool

    text = asyncio.run(
        loop._run_tool(
            {"broken": Broken()}, "broken", {}, context=Context(RuntimeResources())
        )
    )
    assert "[E_BAD]" in text
    assert "no access" in text


def test_a_list_of_pydantic_models_becomes_structured_data():
    @tool
    async def list_pods() -> list[Pod]:
        return [Pod(name="api"), Pod(name="worker")]

    output = asyncio.run(execute_tool(list_pods, {}))
    assert output.data == {"items": [{"name": "api"}, {"name": "worker"}]}
