"""`reactifact.testing.ScenarioLab`/`Scenario` — the core of `reactifact.testing`
had zero direct pytest coverage (only indirect, via the example scenarios
run through the `reactifact scenario` CLI, which `pytest` never executes). This
module exercises `ScenarioLab.run()`, fault injection + tool restoration,
and multi-turn `Scenario` continuity directly, the way `tests/test_tools.py`
and `tests/test_runtime_errors.py` exercise the primitives they wrap.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Patch,
    Produce,
    ProduceCall,
    ResourceKey,
    RuntimeResources,
    produce,
    tool,
)
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.testing import ScenarioLab
from reactifact.tool_use import ToolAnswer, ToolUse


def run(coro):
    return asyncio.run(coro)


class Problem(BaseModel):
    text: str


class Report(BaseModel):
    text: str


tool_calls: dict[str, list[dict]] = {}


@tool
async def kubectl(resource: str) -> str:
    """Query the state of k8s resources."""
    tool_calls.setdefault("kubectl", []).append({"resource": resource})
    return f"status {resource}: ok"


class ScriptedLLM(LLMProvider):
    """Answers from a fixed script (see `tests/test_tools.py`)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request: LLMRequest):
        yield LLMResponse(text="")


class UsageLLM(LLMProvider):
    """Like `ScriptedLLM`, but reports token usage (for the scenario report)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(
            text=text, usage={"prompt_tokens": 10, "completion_tokens": 5}
        )

    async def stream(self, request: LLMRequest):
        yield LLMResponse(text="")


class BuildReport(Produce[Report]):
    artifact_type = Report

    async def produce(self, call: ProduceCall) -> Patch | None:
        a = call.trigger
        if a is None or not isinstance(a.data, ToolAnswer):
            return None
        self.effects.create(Report(text=a.data.text))
        return None


class K8sAgent(Agent):
    name = "k8s"
    consumes = [Consume(Problem), Consume(ToolAnswer)]
    produces = [
        ToolUse(name="k8s", system="Use the kubectl tool.", tools=[kubectl]),
        BuildReport(),
    ]


def _resources() -> RuntimeResources:
    return RuntimeResources(
        llm=ScriptedLLM(
            [
                '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                '{"type":"answer","text":"pods: all good"}',
            ]
        )
    )


def test_run_happy_path_reports_artifacts_path_llm_and_tools():
    tool_calls.clear()
    lab = ScenarioLab([K8sAgent()], resources=_resources)

    result = run(lab.run(Problem(text="check pods")))

    report = result.artifacts(Report).exists()
    assert report.text == "pods: all good"
    result.path.contains("k8s")
    result.llm.max_calls(2)
    result.tools.called("kubectl")
    assert tool_calls["kubectl"] == [{"resource": "pods"}]
    # ToolUse announces its own progress (`context.announce(..., kind="agent")`)
    # — captured without the caller wiring up its own astream() consumer.
    result.events.contains("Deciding next action", kind="agent")
    result.events.min_count(1, kind="agent")


def test_events_not_contains_and_kind_filtering():
    tool_calls.clear()
    lab = ScenarioLab([K8sAgent()], resources=_resources)

    result = run(lab.run(Problem(text="check pods")))

    result.events.not_contains("this never happens")
    result.events.not_contains("Deciding next action", kind="status")  # wrong kind
    assert result.events.count(kind="agent") >= 1
    assert result.events.count(kind="nonexistent-kind") == 0
    assert all(
        e.kind == "agent" for e in result.events.all() if "Deciding" in e.message
    )


def test_fresh_context_per_run_does_not_leak_state():
    lab = ScenarioLab([K8sAgent()], resources=_resources)

    r1 = run(lab.run(Problem(text="first")))
    r2 = run(lab.run(Problem(text="second")))

    assert r1.context is not r2.context
    r1.artifacts(Report).count(1)
    r2.artifacts(Report).count(1)


def test_fail_injects_a_tool_error_then_recovers_on_retry():
    """The agent's LLM sees a tool-failure message and retries — the
    documented caveat in `reactifact/testing/fault.py`: an injected fault does
    not abort the run, it surfaces to the LLM like a real transient failure.
    """
    tool_calls.clear()
    lab = ScenarioLab(
        [K8sAgent()],
        resources=lambda: RuntimeResources(
            llm=ScriptedLLM(
                [
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                    '{"type":"answer","text":"pods: all good after retry"}',
                ]
            )
        ),
    )
    lab.fail("kubectl", ConnectionError("kubectl unreachable"), times=1)

    result = run(lab.run(Problem(text="check pods")))

    calls = result.tools.called("kubectl")
    assert calls[0].error == "kubectl unreachable"
    assert calls[1].error is None
    report = result.artifacts(Report).exists()
    assert report.text == "pods: all good after retry"
    result.errors.none()  # the agent didn't crash, it just saw a tool error


def test_fault_is_one_shot_and_tools_are_restored_after_run():
    lab = ScenarioLab([K8sAgent()], resources=_resources)
    tool_use = next(p for p in K8sAgent.produces if isinstance(p, ToolUse))
    original = tool_use.tools["kubectl"]

    lab.fail("kubectl", RuntimeError("boom"))
    run(lab.run(Problem(text="check pods")))
    assert tool_use.tools["kubectl"] is original  # restored, not left wrapped

    # no re-queued fault -> the second run must not fault again
    result = run(lab.run(Problem(text="check pods again")))
    result.errors.none()
    assert result.tools.called("kubectl")[0].error is None


def test_scenario_turn_shares_context_and_aggregates_across_turns():
    tool_calls.clear()
    lab = ScenarioLab(
        [K8sAgent()],
        resources=lambda: RuntimeResources(
            llm=ScriptedLLM(
                [
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                    '{"type":"answer","text":"pods: all good"}',
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"nodes"}}',
                    '{"type":"answer","text":"nodes: all good"}',
                ]
            )
        ),
    )
    convo = lab.scenario()

    r1 = run(convo.turn(Problem(text="check pods")))
    assert r1.context is convo.context  # same Context reused, not rebuilt

    r2 = run(convo.turn(Problem(text="check nodes")))
    assert r2.context is r1.context

    # both turns' Report artifacts persisted on the one shared context
    r2.artifacts(Report).count(2)
    assert convo.path.times("k8s") >= 2
    convo.tools.called_times("kubectl", 2)
    convo.llm.max_calls(4)
    convo.errors.none()
    convo.events.min_count(2, kind="agent")  # "Deciding next action…" x2 turns


def test_result_report_totals_and_render():
    tool_calls.clear()
    lab = ScenarioLab(
        [K8sAgent()],
        resources=lambda: RuntimeResources(
            llm=UsageLLM(
                [
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                    '{"type":"answer","text":"pods: all good"}',
                ]
            )
        ),
    )

    result = run(lab.run(Problem(text="check pods")))
    report = result.report

    assert report.turns == 1
    assert report.agents == ["k8s"]
    assert report.tool_calls == 1
    assert report.llm_calls == 2
    assert (report.prompt_tokens, report.completion_tokens) == (20, 10)
    assert report.total_tokens == 30
    assert report.errors == 0
    assert report.outcomes == ["completed"]
    assert report.duration_ms >= 0

    # assertion-surface tokens and the report read the same numbers
    assert result.llm.prompt_tokens == 20
    assert result.llm.completion_tokens == 10
    assert result.llm.tokens == report.total_tokens

    data = report.to_dict()
    assert data["total_tokens"] == 30
    assert data["llm_calls"] == 2
    assert data["agents"] == ["k8s"]

    rendered = report.render()
    assert "scenario report" in rendered
    assert "30 total" in rendered
    assert "completed" in rendered


def test_scenario_report_aggregates_across_turns():
    tool_calls.clear()
    lab = ScenarioLab(
        [K8sAgent()],
        resources=lambda: RuntimeResources(
            llm=UsageLLM(
                [
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
                    '{"type":"answer","text":"pods: all good"}',
                    '{"type":"tool_call","tool":"kubectl","args":{"resource":"nodes"}}',
                    '{"type":"answer","text":"nodes: all good"}',
                ]
            )
        ),
    )
    convo = lab.scenario()

    turn1 = run(convo.turn(Problem(text="check pods")))
    assert turn1.report.turns == 1
    assert turn1.report.llm_calls == 2

    run(convo.turn(Problem(text="check nodes")))
    report = convo.report

    assert report.turns == 2
    assert report.llm_calls == 4
    assert report.tool_calls == 2
    assert report.total_tokens == 60
    assert report.agents == ["k8s"]
    assert report.outcomes == ["completed"]


#: A tool list stored typedly (not via `resources.set`), as an app's produce
#: would `resources.require(...)` for its own tool-calling loop.
DECISION_TOOLS: ResourceKey[list] = ResourceKey("decision_tools")


@produce(Report)
async def run_dynamic_tools(call: ProduceCall) -> None:
    tools = call.context.resources.require(DECISION_TOOLS)
    try:
        output = await tools[0].execute({"resource": "pods"})
        text = output.text
    except Exception as exc:  # the injected fault surfaces as an error result
        text = f"failed: {exc}"
    call.effects.create(Report(text=text))
    return None


class DynamicToolsAgent(Agent):
    name = "dynamic"
    consumes = [Consume(Problem)]
    produces = [run_dynamic_tools]


def test_fail_covers_tools_registered_as_typed_resources():
    """`_iter_tool_lists` must scan `_typed`, not just `additional` — otherwise
    `result.tools.called/never_called` silently miss typedly-registered tools."""
    tool_calls.clear()
    resources = RuntimeResources(llm=ScriptedLLM([]))
    resources.register(DECISION_TOOLS, [kubectl])

    lab = ScenarioLab([DynamicToolsAgent()], resources=resources)
    lab.fail("kubectl", ConnectionError("typed tool down"), times=1)

    result = run(lab.run(Problem(text="check pods")))

    calls = result.tools.called("kubectl")
    assert calls, "typed tool call was not recorded"
    assert calls[0].error == "typed tool down"
    result.errors.none()
    assert "typed tool down" in result.artifacts(Report).exists().text
    # wrapped in place, then restored on exit
    assert resources.require(DECISION_TOOLS)[0] is kubectl
