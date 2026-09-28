"""Online evaluation in the devops example (§56)."""

import asyncio

from examples.devops.agents import RouteAgent
from examples.devops.models import UserMsg
from examples.devops.online_eval import (
    build_evaluator,
    devops_online_config,
    routed,
    routed_to,
)
from reactifact import Context, Runtime, RuntimeResources
from reactifact.eval import EvalInput, Example, online_evaluate
from reactifact.tracing import Tracer, TraceStore


def _offline_store(tmp_path, texts):
    store = TraceStore(str(tmp_path / "traces.db"))
    for text in texts:
        context = Context(resources=RuntimeResources())
        context.create(UserMsg(text=text))
        asyncio.run(
            Runtime(context, agents=[RouteAgent()], tracer=Tracer(store=store)).arun()
        )
    return store


def test_routed_evaluator_units():
    k8s = EvalInput(
        example=Example(inputs={}),
        outputs={"artifacts": {"K8sProblem": ['{"text": "x"}']}},
    )
    empty = EvalInput(example=Example(inputs={}), outputs={"artifacts": {}})
    assert routed()(k8s) == 1.0
    assert routed()(empty) == 0.0
    assert routed_to("K8sProblem")(k8s) == 1.0
    assert routed_to("GitlabProblem")(k8s) == 0.0


def test_online_eval_tags_passing_runs(tmp_path):
    store = _offline_store(
        tmp_path,
        [
            "pods are crashlooping",
            "gitlab pipeline failed",
            "run the ansible playbook",
        ],
    )
    report = asyncio.run(online_evaluate(store, devops_online_config()))
    assert report.sampled == 3
    assert report.failed == 0
    assert report.annotated == 3
    tagged = asyncio.run(store.query(tags=["eval"]))
    assert tagged["total"] == 3


def test_online_eval_flags_a_mis_route(tmp_path):
    store = _offline_store(
        tmp_path, ["pods are crashlooping", "gitlab pipeline failed"]
    )
    strict = devops_online_config(evaluators={"k8s_only": routed_to("K8sProblem")})
    report = asyncio.run(online_evaluate(store, strict))
    assert report.failed == 1
    failed = asyncio.run(store.query(tags=["eval:failed"]))
    assert failed["total"] == 1


def test_build_evaluator_runs(tmp_path):
    store = _offline_store(tmp_path, ["pods are crashlooping"])
    evaluator = build_evaluator(store)
    report = asyncio.run(evaluator.run_once())
    assert report.evaluated == 1
    assert evaluator.last_report is report


def test_web_app_exposes_online_eval_router(tmp_path):
    from examples.devops.web import create_app
    from fastapi.testclient import TestClient
    from reactifact.providers import LLMProvider, LLMRequest, LLMResponse

    class ScriptedLLM(LLMProvider):
        def __init__(self, responses):
            self.responses = list(responses)

        async def complete(self, request: LLMRequest) -> LLMResponse:
            text = self.responses.pop(0) if self.responses else "{}"
            return LLMResponse(text=text)

        async def stream(self, request):
            yield LLMResponse(text="")

    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"tool_call","tool":"kubectl_get",'
            '"args":{"resource":"pods","namespace":"default"}}',
            '{"type":"answer","text":"Всё ок."}',
        ]
    )
    app = create_app(llm=llm, store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "почему упал под", "session_id": "s1"},
    ) as response:
        "".join(response.iter_text())

    report = client.post("/api/evals/run").json()
    assert report["evaluated"] >= 1
    assert client.get("/api/evals/report").status_code == 200
    assert "aggregate" in client.get("/api/evals/summary").json()
