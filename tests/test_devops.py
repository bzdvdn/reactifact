import asyncio

import pytest
from examples.devops.agents import (
    AnsibleAgent,
    GitlabAgent,
    K8sAgent,
    RenderAgent,
    RouteAgent,
)
from examples.devops.guardrails import (
    ProductionChangeGuardrail,
    SecretRedactionGuardrail,
    devops_guardrail_policy,
    screen,
)
from examples.devops.models import (
    ChatReply,
    GitlabReport,
    K8sProblem,
    K8sReport,
    UserMsg,
)
from examples.devops.tools import CALLS
from reactifact import (
    Budget,
    Context,
    PendingQuestion,
    Runtime,
    RuntimeResources,
)
from reactifact.guardrails import GuardrailViolation
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse


class ScriptedLLM(LLMProvider):
    """The LLM "decides" which tool to call and when to answer."""

    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


def build_runtime(llm):
    resources = RuntimeResources(llm=llm)
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx,
        agents=[
            RouteAgent(),
            K8sAgent(),
            GitlabAgent(),
            AnsibleAgent(),
            RenderAgent(),
        ],
        budget=Budget(max_runs=200, max_tool_calls=12),
    )
    return ctx, runtime


def replies_for(ctx, query_id):
    return [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query_id]


def test_k8s_flow_uses_tool_and_replies():
    CALLS.clear()
    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"tool_call","tool":"kubectl_get","args":{"resource":"pods","namespace":"default"}}',
            '{"type":"answer","text":"Под worker-2f1 в CrashLoopBackOff — смотрим логи."}',
        ]
    )
    ctx, runtime = build_runtime(llm)
    msg = ctx.create(UserMsg(text="почему падает под в кластере?"))
    asyncio.run(runtime.arun())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert "CrashLoopBackOff" in replies[0].data.text
    assert CALLS["kubectl_get"] == 1
    # other agents were not woken up
    assert ctx.list_artifacts(K8sReport)
    assert ctx.list_artifacts(GitlabReport) == []


def test_gitlab_flow_replies():
    CALLS.clear()
    llm = ScriptedLLM(
        [
            '{"target":"gitlab"}',
            '{"type":"tool_call","tool":"gitlab_pipeline","args":{"project":"payments"}}',
            '{"type":"answer","text":"Пайплайн #4821 упал на deploy."}',
        ]
    )
    ctx, runtime = build_runtime(llm)
    msg = ctx.create(UserMsg(text="статус пайплайна в gitlab?"))

    async def run():
        async for _ in runtime.astream():
            pass

    asyncio.run(run())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert "deploy" in replies[0].data.text
    assert CALLS["gitlab_pipeline"] == 1


def test_help_when_no_match():
    llm = ScriptedLLM(['{"target":"none"}'])
    ctx, runtime = build_runtime(llm)
    msg = ctx.create(UserMsg(text="привет"))
    asyncio.run(runtime.arun())
    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert "k8s" in replies[0].data.text
    assert "GitLab" in replies[0].data.text


def test_two_agents_in_one_session_no_crossfire():
    CALLS.clear()
    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"tool_call","tool":"kubectl_get","args":{"resource":"pods","namespace":"default"}}',
            '{"type":"answer","text":"k8s: ок"}',
            '{"target":"gitlab"}',
            '{"type":"tool_call","tool":"gitlab_pipeline","args":{"project":"api"}}',
            '{"type":"answer","text":"gitlab: пайплайн упал"}',
        ]
    )
    ctx, runtime = build_runtime(llm)

    m1 = ctx.create(UserMsg(text="почему падает pod в кластере?"))
    asyncio.run(runtime.arun())
    m2 = ctx.create(UserMsg(text="статус пайплайна gitlab?"))
    asyncio.run(runtime.arun())

    r1 = replies_for(ctx, m1.id)
    r2 = replies_for(ctx, m2.id)
    assert len(r1) == 1 and "k8s: ок" in r1[0].data.text
    assert len(r2) == 1 and "gitlab: пайплайн упал" in r2[0].data.text
    assert CALLS["kubectl_get"] == 1
    assert CALLS["gitlab_pipeline"] == 1


def test_k8s_agent_asks_namespace_and_continues():
    """HITL: the k8s agent asks for namespace, the human answers, the loop continues."""
    CALLS.clear()
    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"ask","text":"В каком namespace разворачивается clickhouse?"}',
            '{"type":"tool_call","tool":"kubectl_get","args":{"resource":"deployments","namespace":"production"}}',
            '{"type":"answer","text":"В production деплой clickhouse в CrashLoopBackOff."}',
        ]
    )
    ctx, runtime = build_runtime(llm)
    msg = ctx.create(UserMsg(text="у меня не раскатывается clickhouse"))
    asyncio.run(runtime.arun())

    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "clarify"
    assert "namespace" in questions[0].data.question
    assert "kubectl_get" not in CALLS  # tool was not called before the clarification

    ctx.resume(questions[0].id, "production")
    asyncio.run(runtime.arun())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert "CrashLoopBackOff" in replies[0].data.text
    assert CALLS["kubectl_get"] == 1


# --------------------------------------------------------------------------- #
# Trust & safety — custom guardrails in the devops demo
# --------------------------------------------------------------------------- #


def test_custom_guardrail_blocks_prod_change_without_ticket():
    ctx = Context(resources=RuntimeResources(guardrails=devops_guardrail_policy()))
    ctx.create(UserMsg(text="delete the prod pods"))
    with pytest.raises(GuardrailViolation):
        asyncio.run(Runtime(ctx, agents=[RouteAgent()]).arun())
    assert ctx.list_artifacts(K8sProblem) == []


def test_custom_guardrail_allows_a_ticketed_change():
    ctx = Context(resources=RuntimeResources(guardrails=devops_guardrail_policy()))
    ctx.create(UserMsg(text="delete the prod pods CHG-1042"))
    asyncio.run(Runtime(ctx, agents=[RouteAgent()]).arun())
    assert ctx.list_artifacts(K8sProblem)


def test_custom_guardrail_decision_defers_to_policy():
    guardrail = ProductionChangeGuardrail()
    ctx = Context(resources=RuntimeResources())
    blocked = guardrail.check(K8sProblem(text="drop the production database"), ctx)
    assert blocked.action == "violation"
    allowed = guardrail.check(K8sProblem(text="drop the production db CHG-7"), ctx)
    assert allowed.action == "allow"


def test_secret_redaction_guardrail_rewrites_the_model():
    ctx = Context(resources=RuntimeResources())
    decision = SecretRedactionGuardrail().check(
        UserMsg(text="token=abc123 and password: hunter2"), ctx
    )
    assert decision.action == "redact"
    assert "abc123" not in decision.data.text
    assert "hunter2" not in decision.data.text
    assert "[REDACTED]" in decision.data.text


def test_builtin_pii_guardrail_redacts_a_produced_problem():
    ctx = Context(resources=RuntimeResources(guardrails=devops_guardrail_policy()))
    ctx.create(UserMsg(text="pods crash, mail me at ops@corp.com"))
    asyncio.run(Runtime(ctx, agents=[RouteAgent()]).arun())
    problem = ctx.list_artifacts(K8sProblem)[0]
    assert "[REDACTED:email]" in problem.data.text


def test_screen_refuses_an_unsafe_raw_input():
    ctx = Context(resources=RuntimeResources())
    decision = screen(devops_guardrail_policy(), ctx, "wipe the prod cluster pods")
    assert decision.action == "block"
    assert decision.guardrail == "production_change"


def test_flag_policy_allows_and_does_not_raise():
    ctx = Context(
        resources=RuntimeResources(
            guardrails=devops_guardrail_policy(on_violation="flag")
        )
    )
    ctx.create(UserMsg(text="delete the prod pods"))
    asyncio.run(Runtime(ctx, agents=[RouteAgent()]).arun())
    assert ctx.list_artifacts(K8sProblem)
