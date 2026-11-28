import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    PendingQuestion,
    Runtime,
    RuntimeResources,
    Step,
    StepInvocation,
    tool,
)
from reactifact.effects import Effects
from reactifact.llm_agent import HITLLMAgent, LLMAgent
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.tools import (
    Tool,
    ToolError,
    ToolOutput,
    begin_tool_runs,
    end_tool_runs,
    execute_tool,
    tool_runs,
)
from reactifact.tools.use import DeferredToolGroup, ToolAnswer, ToolUse, ToolUseHITL


class K8sProblems(BaseModel):
    text: str


class GitlabProblems(BaseModel):
    text: str


class AnsibleProblems(BaseModel):
    text: str


class K8sReport(BaseModel):
    text: str


class GitlabReport(BaseModel):
    text: str


class ScriptedLLM(LLMProvider):
    """Answers from a script: the LLM "decides" which tool to call and when to answer."""

    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


calls: dict[str, list[dict]] = {}


@tool
async def kubectl(resource: str) -> str:
    """Query the state of k8s resources."""
    calls.setdefault("kubectl", []).append({"resource": resource})
    return f"status {resource}: ok"


@tool
async def gitlab_search(query: str) -> str:
    """Search GitLab repositories."""
    calls.setdefault("gitlab_search", []).append({"query": query})
    return f"нашлось по «{query}»: 2 MR"


@tool(destructive=True)
async def delete_pod(name: str) -> str:
    """Delete a k8s pod."""
    calls.setdefault("delete_pod", []).append({"name": name})
    return f"deleted {name}"


def test_tool_decorator_builds_schema():
    t = kubectl
    assert t.name == "kubectl"
    assert "k8s" in t.description.lower()
    assert t.schema["required"] == ["resource"]
    assert t.schema["properties"]["resource"]["type"] == "string"


def test_single_agent_loop_and_report():
    """ToolUse (container agent) runs the loop, its produce builds the report."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
            '{"type":"answer","text":"pods: все в порядке"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [
            ToolUse(
                name="k8s",
                system="Ты эксперт по k8s. Используй tool kubectl.",
                tools=[kubectl],
            ),
            BuildReport(),
        ]

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="проверь pods"))
    asyncio.run(runtime.arun())

    reports = [r for r in ctx.list_artifacts(K8sReport)]
    assert len(reports) == 1
    assert reports[0].data.text == "pods: все в порядке"
    assert calls["kubectl"] == [{"resource": "pods"}]


def test_deferred_tool_group_loads_on_demand_once():
    """`load_tools` reveals a group's real tool only after the LLM asks for
    it, and the loader runs at most once per run even if asked twice."""
    calls.clear()
    loader_calls = 0

    @tool
    async def extra_tool(x: str) -> str:
        """An extra tool, hidden behind a deferred group."""
        calls.setdefault("extra_tool", []).append({"x": x})
        return f"extra: {x}"

    async def loader():
        nonlocal loader_calls
        loader_calls += 1
        return [extra_tool]

    group = DeferredToolGroup(
        group_id="mcp:extra",
        display_name="Extra",
        description="Extra tools",
        tool_names=["extra_tool"],
        loader=loader,
    )

    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"load_tools","args":{"group_id":"mcp:extra"}}',
            '{"type":"tool_call","tool":"load_tools","args":{"group_id":"mcp:extra"}}',
            '{"type":"tool_call","tool":"extra_tool","args":{"x":"1"}}',
            '{"type":"answer","text":"done"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class DeferredAgent(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [
            ToolUse(
                name="deferred",
                system="Use load_tools to discover extra tools before using them.",
                tools=[],
                deferred_tool_groups=[group],
                max_steps=6,
            ),
            BuildReport(),
        ]

    runtime = Runtime(ctx, agents=[DeferredAgent()])
    ctx.create(K8sProblems(text="go"))
    asyncio.run(runtime.arun())

    reports = ctx.list_artifacts(K8sReport)
    assert len(reports) == 1
    assert reports[0].data.text == "done"
    assert calls["extra_tool"] == [{"x": "1"}]
    assert loader_calls == 1  # cached after the first load_tools call


def test_two_agents_do_not_crossfire():
    """Two LLM agents: the runtime wakes only the one whose ToolAnswer matches (by agent field)."""
    calls.clear()
    invoked = {"k8s_report": 0, "gitlab_report": 0}
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
            '{"type":"answer","text":"k8s: ок"}',
            '{"type":"tool_call","tool":"gitlab_search","args":{"query":"auth"}}',
            '{"type":"answer","text":"gitlab: 2 MR"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildK8sReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            invoked["k8s_report"] += 1
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class BuildGitlabReport(Step[GitlabReport]):
        artifact_type = GitlabReport

        async def run(self, call: StepInvocation):
            invoked["gitlab_report"] += 1
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(GitlabReport(text=a.data.text))

    class K8sAgent(Agent):
        consumes = [
            Consume(K8sProblems),
            Consume.by_field(ToolAnswer, "agent", "k8s"),  # only its own answers
        ]
        steps = [
            ToolUse(
                name="k8s",
                system="Ты эксперт по k8s. Используй tool kubectl.",
                tools=[kubectl],
            ),
            BuildK8sReport(),
        ]

    class GitlabAgent(Agent):
        # reacts to GitlabProblems and AnsibleProblems; woken only by its own answers
        consumes = [
            Consume(GitlabProblems),
            Consume(AnsibleProblems),
            Consume.by_field(ToolAnswer, "agent", "gitlab"),
        ]
        steps = [
            ToolUse(
                name="gitlab",
                system="Ты эксперт по GitLab. Используй tool gitlab_search.",
                tools=[gitlab_search],
            ),
            BuildGitlabReport(),
        ]

    runtime = Runtime(ctx, agents=[K8sAgent(), GitlabAgent()])
    ctx.create(K8sProblems(text="проверь pods"))
    asyncio.run(runtime.arun())
    # ToolAnswer(k8s) did NOT wake GitlabAgent: its report-builder was never called
    assert invoked["gitlab_report"] == 0

    ctx.create(GitlabProblems(text="найди MR по auth"))
    asyncio.run(runtime.arun())

    k8s_reports = ctx.list_artifacts(K8sReport)
    gitlab_reports = ctx.list_artifacts(GitlabReport)
    assert len(k8s_reports) == 1 and k8s_reports[0].data.text == "k8s: ок"
    assert len(gitlab_reports) == 1 and gitlab_reports[0].data.text == "gitlab: 2 MR"
    assert calls["kubectl"] == [{"resource": "pods"}]
    assert calls["gitlab_search"] == [{"query": "auth"}]
    assert invoked["gitlab_report"] >= 1  # woke on its own artifact and answer


def test_tool_failure_is_returned_to_llm():

    @tool
    async def flaky() -> str:
        raise RuntimeError("upstream boom")

    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"flaky","args":{}}',
            '{"type":"answer","text":"Источник недоступен."}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class Agent1(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [ToolUse("У тебя есть тул flaky.", [flaky]), BuildReport()]

    runtime = Runtime(ctx, agents=[Agent1()])
    ctx.create(K8sProblems(text="проверь"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(K8sReport)[0].data.text == "Источник недоступен."


def test_budget_max_tool_calls_stops_loop():
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"a"}}',
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"b"}}',
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"c"}}',
            '{"type":"answer","text":"хватит"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class Agent1(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [ToolUse("агент", [kubectl]), BuildReport()]

    runtime = Runtime(ctx, agents=[Agent1()], budget=Budget(max_tool_calls=2))
    ctx.create(K8sProblems(text="много"))
    asyncio.run(runtime.arun())

    assert len(calls["kubectl"]) == 2  # limit not exceeded
    assert ctx.list_artifacts(K8sReport)[0].data.text == "хватит"


def test_unknown_tool_reported_to_llm():
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"nope","args":{}}',
            '{"type":"answer","text":"такого тула нет"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class Agent1(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [ToolUse("агент", [kubectl]), BuildReport()]

    runtime = Runtime(ctx, agents=[Agent1()])
    ctx.create(K8sProblems(text="х"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(K8sReport)[0].data.text == "такого тула нет"


class ChatReply(BaseModel):
    text: str


def test_llm_agent_without_consumes_raises():
    """Empty consumes = the agent never runs — an honest error at build time."""
    try:

        class NoTrigger(LLMAgent):
            system = "агент"
            tools = [kubectl]

        NoTrigger()
    except ValueError as exc:
        assert "no consumes" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_llm_agent_merges_explicit_steps_with_the_tool_loop():
    class ExplicitAgent(LLMAgent):
        system = "Use kubectl."
        tools = [kubectl]
        consumes = [Consume(K8sProblems)]

    agent = ExplicitAgent(steps=[Step(ChatReply)])

    assert any(isinstance(step, ToolUse) for step in agent.steps or ())
    assert any(
        isinstance(step, Step) and step.artifact_type is ChatReply
        for step in agent.steps or ()
    )


def test_llm_agent_tools_only_no_produces():
    """Without produces: ToolAnswer is the result, rendered by the base chat."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods"}}',
            '{"type":"answer","text":"pods: все в порядке"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class HelperAgent(LLMAgent):
        system = "Ты эксперт по k8s. Используй tool kubectl."
        tools = [kubectl]
        consumes = [Consume(K8sProblems)]
        # no produces — ToolAnswer itself is the result

    class RenderFromBaseChat(Step[ChatReply]):
        artifact_type = ChatReply

        async def run(self, call: StepInvocation):
            a = call.trigger
            if (
                a is None
                or not isinstance(a.data, ToolAnswer)
                or a.data.agent != "helperagent"
            ):
                return None
            call.effects.create(ChatReply(text=a.data.text))

    class BaseChat(Agent):
        consumes = [Consume.by_field(ToolAnswer, "agent", "helperagent")]
        steps = [RenderFromBaseChat()]

    runtime = Runtime(ctx, agents=[HelperAgent(), BaseChat()])
    ctx.create(K8sProblems(text="проверь pods"))
    asyncio.run(runtime.arun())

    replies = ctx.list_artifacts(ChatReply)
    assert len(replies) == 1
    assert replies[0].data.text == "pods: все в порядке"
    assert calls["kubectl"] == [{"resource": "pods"}]


def test_forced_answer_when_loop_hits_step_limit():
    """The loop hit max_steps → a forced LLM answer, not a dry status."""
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"a"}}',
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"b"}}',
            '{"text":"Вынужденный ответ по данным."}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class Agent1(Agent):
        consumes = [Consume(K8sProblems), Consume(ToolAnswer)]
        steps = [ToolUse("агент", [kubectl], max_steps=2), BuildReport()]

    runtime = Runtime(ctx, agents=[Agent1()])
    ctx.create(K8sProblems(text="диагностируй"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(K8sReport)[0].data.text == "Вынужденный ответ по данным."


def test_hitl_agent_asks_clarifying_question_and_resumes():
    """HITLLMAgent: the LLM asks for clarification (namespace) → the human answers → the loop continues."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"ask","text":"В каком namespace?"}',
            '{"type":"tool_call","tool":"kubectl","args":{"resource":"pods","namespace":"default"}}',
            '{"type":"answer","text":"В default всё ок"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(HITLLMAgent):
        system = "Ты эксперт по k8s. Используй tool kubectl."
        tools = [kubectl]
        consumes = [Consume(K8sProblems)]
        steps = [BuildReport()]
        resume_announce = staticmethod(lambda a: f"Принято: «{a}». Проверяю…")

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="почему падает под?"))
    asyncio.run(runtime.arun())

    # a clarifying question was asked, the tool hasn't been called yet
    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.question == "В каком namespace?"
    assert questions[0].data.kind == "clarify"
    assert "kubectl" not in calls

    # the human answers → after the answer an announce is visible (kind="status")
    async def collect_resume():
        return [ev async for ev in runtime.astream()]

    ctx.resume(questions[0].id, "default")
    events = asyncio.run(collect_resume())

    statuses = [e.message for e in events if e.kind == "status"]
    assert any("Принято: «default». Проверяю…" in m for m in statuses)

    reports = ctx.list_artifacts(K8sReport)
    assert len(reports) == 1
    assert reports[0].data.text == "В default всё ок"
    # the tool was called exactly once, after the clarification (namespace from the tool signature discarded)
    assert calls["kubectl"] == [{"resource": "pods"}]


def test_max_asks_caps_rephrased_clarifications():
    """The LLM rephrased the question — max_asks=1 prevents a second ask."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"ask","text":"В каком namespace?"}',
            '{"type":"ask","text":"Укажите namespace, пожалуйста"}',
            '{"type":"answer","text":"ок"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(HITLLMAgent):
        system = "агент"
        tools = [kubectl]
        consumes = [Consume(K8sProblems)]
        steps = [BuildReport()]
        max_asks = 1

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="диагностируй"))
    asyncio.run(runtime.arun())

    # exactly one question was asked; the second (rephrased) one was not created
    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.question == "В каком namespace?"

    ctx.resume(questions[0].id, "test")
    asyncio.run(runtime.arun())
    # after the answer the loop continued and gave the final answer
    assert ctx.list_artifacts(K8sReport)[0].data.text == "ок"


def test_hitl_destructive_tool_requires_approval_then_runs():
    """A destructive tool is offered but not run until a human approves it."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"delete_pod","args":{"name":"web-1"}}',
            '{"type":"answer","text":"Под web-1 удалён"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(HITLLMAgent):
        system = "агент"
        tools = [delete_pod]
        consumes = [Consume(K8sProblems)]
        steps = [BuildReport()]

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="почисти под web-1"))
    asyncio.run(runtime.arun())

    # the tool call is gated behind approval, not executed yet
    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "approve"
    assert questions[0].data.notes["tool_id"] == "delete_pod"
    assert "delete_pod" not in calls

    ctx.resume(questions[0].id, {"approved": True})
    asyncio.run(runtime.arun())

    assert calls["delete_pod"] == [{"name": "web-1"}]
    assert ctx.list_artifacts(K8sReport)[0].data.text == "Под web-1 удалён"


def test_hitl_destructive_tool_denied_is_not_executed():
    """A denied approval never runs the tool; the loop continues without it."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"delete_pod","args":{"name":"web-1"}}',
            '{"type":"answer","text":"Не стал удалять"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(HITLLMAgent):
        system = "агент"
        tools = [delete_pod]
        consumes = [Consume(K8sProblems)]
        steps = [BuildReport()]

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="почисти под web-1"))
    asyncio.run(runtime.arun())

    questions = ctx.list_artifacts(PendingQuestion)
    assert questions[0].data.kind == "approve"

    ctx.resume(questions[0].id, "no")
    asyncio.run(runtime.arun())

    assert "delete_pod" not in calls
    assert ctx.list_artifacts(K8sReport)[0].data.text == "Не стал удалять"


def test_max_approvals_caps_repeated_destructive_calls():
    """max_approvals=1 stops a second approval request for the same call."""
    calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"delete_pod","args":{"name":"web-1"}}',
            '{"type":"tool_call","tool":"delete_pod","args":{"name":"web-2"}}',
            '{"type":"answer","text":"готово"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    class BuildReport(Step[K8sReport]):
        artifact_type = K8sReport

        async def run(self, call: StepInvocation):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            call.effects.create(K8sReport(text=a.data.text))

    class K8sAgent(HITLLMAgent):
        system = "агент"
        tools = [delete_pod]
        consumes = [Consume(K8sProblems)]
        steps = [BuildReport()]
        max_approvals = 1

    runtime = Runtime(ctx, agents=[K8sAgent()])
    ctx.create(K8sProblems(text="почисти поды"))
    asyncio.run(runtime.arun())

    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.notes["args"] == {"name": "web-1"}

    ctx.resume(questions[0].id, "yes")
    asyncio.run(runtime.arun())

    # the first call ran, the second (different args) hit the approval cap
    # instead of asking again, and the loop still reached a final answer
    assert calls["delete_pod"] == [{"name": "web-1"}]
    assert len(ctx.list_artifacts(PendingQuestion)) == 1
    assert ctx.list_artifacts(K8sReport)[0].data.text == "готово"


class ErrorInfoTool(Tool):
    name = "broken"
    description = "returns only a typed error"
    schema = {}

    async def execute(self, args):
        return ToolOutput(
            error_info=ToolError(code="E_BROKEN", message="boom", retryable=True)
        )


def test_error_info_only_failure_is_recorded_and_returned() -> None:
    token = begin_tool_runs()
    try:
        output = asyncio.run(execute_tool(ErrorInfoTool(), {}))
        runs = tool_runs()
    finally:
        end_tool_runs(token)

    assert output.ok is False
    assert runs[-1].ok is False
    assert runs[-1].error is not None
    assert "boom" in runs[-1].error


def test_tool_use_reports_error_info_only_failure() -> None:
    step = ToolUse("report failures", [ErrorInfoTool()])
    context = Context(resources=RuntimeResources())

    result = asyncio.run(
        step._run_tool(
            step.tools,
            "broken",
            {},
            context=context,
        )
    )

    assert "E_BROKEN" in result
    assert "boom" in result


def test_deferred_tool_group_accepts_a_sync_loader() -> None:
    @tool
    async def extra_tool(x: str) -> str:
        """An extra tool."""
        return x

    group = DeferredToolGroup(
        group_id="sync",
        display_name="Sync",
        description="sync loader",
        tool_names=["extra_tool"],
        loader=lambda: [extra_tool],
    )
    step = ToolUse("tools", [], deferred_tool_groups=[group])
    loaded: dict[str, dict[str, Tool]] = {}

    message = asyncio.run(step._load_tool_group(loaded, {"group_id": "sync"}))

    assert "Loaded" in message
    assert "extra_tool" in loaded["sync"]


def test_approval_questions_do_not_consume_max_asks() -> None:
    context = Context(resources=RuntimeResources())
    context.create(
        PendingQuestion(
            question="Approve?",
            kind="approve",
            notes={"query_id": "q1"},
        )
    )
    step = ToolUseHITL("ask", [], max_asks=1)
    effects = Effects(context)

    step._handle_ask(context, effects, "q1", [], "What namespace?")

    created = [
        operation.data
        for operation in effects.operations
        if isinstance(operation.data, PendingQuestion)
    ]
    assert [question.question for question in created] == ["What namespace?"]
    assert created[0].kind == "clarify"
