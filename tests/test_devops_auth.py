"""devops identity, authorization and curated memory.

`admin1` is an admin, `admin2` an analyst without `run:ansible`. Long-term
memory is per-user: a report summary written in one namespace is invisible to
the other, and every entry points back at the session/artifact it came from.
"""

import asyncio

from examples.devops.agents import (
    AnsibleAgent,
    GitlabAgent,
    K8sAgent,
    MemoryAgent,
    RenderAgent,
    RouteAgent,
)
from examples.devops.auth import POLICY, PRINCIPALS, build_resources
from examples.devops.models import AnsibleReport, ChatReply, K8sReport, UserMsg
from reactifact import Budget, Context, PendingAction, Runtime
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.recipes import MemoryStore


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


def build_runtime(resources, dispatcher=MemoryStore.dispatch):
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx,
        agents=[
            RouteAgent(),
            K8sAgent(),
            GitlabAgent(),
            AnsibleAgent(),
            RenderAgent(),
            MemoryAgent(),
        ],
        dispatcher=dispatcher,
        budget=Budget(max_runs=200, max_tool_calls=12),
    )
    return ctx, runtime


def replies_for(ctx, query_id):
    return [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query_id]


def test_principals_and_policy():
    assert set(PRINCIPALS) == {"admin1", "admin2"}
    assert POLICY.authorize(PRINCIPALS["admin1"], "run", "ansible")
    assert not POLICY.authorize(PRINCIPALS["admin2"], "run", "ansible")
    assert POLICY.authorize(PRINCIPALS["admin2"], "run", "k8s")
    assert not POLICY.authorize(PRINCIPALS["admin2"], "execute", "ansible_run")


def test_admin2_is_denied_before_the_ansible_agent_runs(tmp_path):
    llm = ScriptedLLM(['{"target":"ansible"}'])
    ctx, runtime = build_runtime(build_resources(llm, "admin2", tmp_path))

    msg = ctx.create(UserMsg(text="запусти playbook деплоя"))
    asyncio.run(runtime.arun())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert replies[0].data.kind == "denied"
    assert "Access denied" in replies[0].data.text
    assert ctx.list_artifacts(AnsibleReport) == []


def test_admin1_runs_ansible_and_indexes_the_report(tmp_path):
    llm = ScriptedLLM(
        [
            '{"target":"ansible"}',
            '{"type":"tool_call","tool":"ansible_run","args":'
            '{"playbook":"deploy.yml","hosts":"web","role":"deploy"}}',
            '{"type":"answer","text":"Playbook deploy.yml ok on web"}',
        ]
    )
    resources = build_resources(llm, "admin1", tmp_path)
    ctx, runtime = build_runtime(resources)

    msg = ctx.create(UserMsg(text="прогони playbook деплоя", session_id="admin1:s1"))
    asyncio.run(runtime.arun())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1 and "Playbook" in replies[0].data.text
    report = ctx.list_artifacts(AnsibleReport)[0]

    memory = resources.require(MemoryStore)
    hits = asyncio.run(memory.search("playbook", tags={"kind": "report"}))
    assert len(hits) == 1
    record = hits[0].record
    assert record.source_session == "admin1:s1"
    assert record.source_artifact == report.id
    assert record.source_version == report.version
    assert record.tags["system"] == "ansible"

    # the source session is the unit of erasure (GDPR-style forget)
    assert asyncio.run(memory.forget_source("admin1:s1")) == 1


def test_memory_intent_is_not_written_before_dispatch(tmp_path):
    """The step records a PendingAction; without a dispatcher memory is untouched."""
    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"tool_call","tool":"kubectl_get","args":'
            '{"resource":"pods","namespace":"production"}}',
            '{"type":"answer","text":"worker-2f1 OOMKilled in production"}',
        ]
    )
    resources = build_resources(llm, "admin1", tmp_path)
    ctx, runtime = build_runtime(resources, dispatcher=None)
    ctx.create(UserMsg(text="почему падает под?", session_id="admin1:s1"))

    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(K8sReport)
    actions = ctx.list_artifacts(PendingAction)
    assert len(actions) == 1
    assert actions[0].data.status == "pending"
    assert asyncio.run(resources.require(MemoryStore).list()) == []


def test_broken_memory_does_not_block_routing(tmp_path, caplog):
    """Recall is optional: a failing MemoryStore must not kill the turn."""
    llm = ScriptedLLM(['{"target":"none"}'])
    resources = build_resources(llm, "admin1", tmp_path)

    class BrokenMemory:
        async def search(self, *args, **kwargs):
            raise RuntimeError("memory backend down")

    resources.register(MemoryStore, BrokenMemory())
    ctx, runtime = build_runtime(resources)
    msg = ctx.create(UserMsg(text="привет"))

    with caplog.at_level("WARNING"):
        asyncio.run(runtime.arun())

    replies = replies_for(ctx, msg.id)
    assert len(replies) == 1
    assert "I help with k8s" in replies[0].data.text
    assert "memory recall failed" in caplog.text


def test_memory_is_per_user_and_recalled_into_routing(tmp_path):
    first = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"tool_call","tool":"kubectl_get","args":'
            '{"resource":"pods","namespace":"production"}}',
            '{"type":"answer","text":"worker-2f1 OOMKilled in production"}',
        ]
    )
    admin1 = build_resources(first, "admin1", tmp_path)
    ctx, runtime = build_runtime(admin1)
    ctx.create(UserMsg(text="почему падает под в кластере?", session_id="admin1:s1"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(K8sReport)

    # second session, same user: the previous incident is recalled into routing
    router = ScriptedLLM(['{"target":"none"}'])
    second_admin1 = build_resources(router, "admin1", tmp_path)
    ctx2, runtime2 = build_runtime(second_admin1)
    msg2 = ctx2.create(UserMsg(text="что было с worker-2f1?", session_id="admin1:s2"))
    asyncio.run(runtime2.arun())
    route_prompt = "\n".join(message.content for message in router.requests[0].messages)
    assert "Known context" in route_prompt
    assert "worker-2f1 OOMKilled" in route_prompt
    assert replies_for(ctx2, msg2.id)

    # same store dir, different user namespace: nothing leaks
    admin2 = build_resources(ScriptedLLM([]), "admin2", tmp_path)
    assert asyncio.run(admin2.require(MemoryStore).search("worker-2f1")) == []
    # and the source session can be forgotten without touching other entries
    assert (
        asyncio.run(second_admin1.require(MemoryStore).forget_source("admin1:s1")) == 1
    )
    assert asyncio.run(second_admin1.require(MemoryStore).search("worker-2f1")) == []
