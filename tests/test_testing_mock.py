"""`reactifact.testing.mock` — generic resource-level fault injection.

Two layers of coverage: direct/white-box tests of `_FailingProxy` and
`ResourceFaultInstaller` (precise control over sync/async/async-generator
methods, restoration, and misuse errors), and an end-to-end test through
`ScenarioLab.fail_resource("llm", ...)` against a real `ToolUse` agent,
proving the honest-fallback path (§59) actually kicks in when the model
resource fails — the "mock a part, force it to error, check the system's
behavior" pattern.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel
from reactifact import Agent, Consume, ResourceKey, RuntimeResources, produce, tool
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.testing import ScenarioError, ScenarioLab
from reactifact.testing.mock import ResourceFault, ResourceFaultInstaller, _FailingProxy
from reactifact.tool_use import ToolAnswer, ToolUse


def run(coro):
    return asyncio.run(coro)


class FakeResource:
    def __init__(self) -> None:
        self.sync_calls: list[int] = []
        self.async_calls: list[int] = []
        self.const = "unwrapped-attribute"

    def sync_method(self, x: int) -> str:
        self.sync_calls.append(x)
        return f"sync:{x}"

    async def async_method(self, x: int) -> str:
        self.async_calls.append(x)
        return f"async:{x}"

    async def async_gen_method(self, x: int):
        yield f"chunk1:{x}"
        yield f"chunk2:{x}"


# --- _FailingProxy: white-box ------------------------------------------------- #


def test_proxy_passes_through_non_callable_attributes():
    proxy = _FailingProxy(FakeResource(), ResourceFault("r", RuntimeError("boom")))
    assert proxy.const == "unwrapped-attribute"


def test_proxy_fails_every_method_by_default():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        proxy.sync_method(1)
    with pytest.raises(RuntimeError, match="boom"):
        run(proxy.async_method(2))
    assert fake.sync_calls == []
    assert fake.async_calls == []


def test_proxy_can_target_a_single_method():
    fake = FakeResource()
    proxy = _FailingProxy(
        fake, ResourceFault("r", RuntimeError("boom"), method="async_method")
    )

    assert proxy.sync_method(1) == "sync:1"  # untouched
    with pytest.raises(RuntimeError, match="boom"):
        run(proxy.async_method(2))
    assert fake.sync_calls == [1]
    assert fake.async_calls == []


def test_proxy_times_faults_then_recovers():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", RuntimeError("boom"), times=2))

    with pytest.raises(RuntimeError):
        proxy.sync_method(1)
    with pytest.raises(RuntimeError):
        proxy.sync_method(2)
    assert proxy.sync_method(3) == "sync:3"  # times exhausted, delegates for real
    assert fake.sync_calls == [3]


def test_proxy_fails_an_async_generator_before_the_first_yield():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", RuntimeError("boom")))

    async def drain():
        return [chunk async for chunk in proxy.async_gen_method(1)]

    with pytest.raises(RuntimeError, match="boom"):
        run(drain())


def test_proxy_error_factory_gets_a_fresh_exception_per_call():
    fake = FakeResource()
    made: list[Exception] = []

    def make_error() -> Exception:
        exc = RuntimeError("fresh")
        made.append(exc)
        return exc

    proxy = _FailingProxy(fake, ResourceFault("r", make_error))
    try:
        proxy.sync_method(1)
    except RuntimeError as exc:
        assert exc is made[0]
    try:
        proxy.sync_method(2)
    except RuntimeError as exc:
        assert exc is made[1]
    assert made[0] is not made[1]


def test_proxy_returns_a_stub_value_instead_of_delegating():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", returns="stub"))

    assert proxy.sync_method(1) == "stub"
    assert fake.sync_calls == []  # never reached the real resource


def test_proxy_stub_returns_none_is_distinguishable_from_no_stub():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", returns=None))

    assert proxy.sync_method(1) is None
    assert fake.sync_calls == []


def test_proxy_side_effect_list_sequences_values_and_exceptions():
    fake = FakeResource()
    proxy = _FailingProxy(
        fake, ResourceFault("r", side_effect=["a", RuntimeError("boom"), "b"])
    )

    assert proxy.sync_method(1) == "a"
    with pytest.raises(RuntimeError, match="boom"):
        proxy.sync_method(2)
    assert proxy.sync_method(3) == "b"
    # list exhausted -> delegates to the real resource
    assert proxy.sync_method(4) == "sync:4"


def test_proxy_when_narrows_the_fault_by_args():
    fake = FakeResource()
    proxy = _FailingProxy(
        fake, ResourceFault("r", RuntimeError("boom"), when=lambda a, k: a == (7,))
    )

    assert proxy.sync_method(1) == "sync:1"  # non-matching -> delegates
    with pytest.raises(RuntimeError, match="boom"):
        proxy.sync_method(7)


def test_proxy_delay_sleeps_before_faulting():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", RuntimeError("boom"), delay=0.05))

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        proxy.sync_method(1)
    assert time.monotonic() - started >= 0.05


def test_proxy_stub_works_on_async_methods_too():
    fake = FakeResource()
    proxy = _FailingProxy(fake, ResourceFault("r", returns="async-stub"))

    assert run(proxy.async_method(1)) == "async-stub"
    assert fake.async_calls == []


# --- ResourceFaultInstaller: white-box ---------------------------------------- #


def test_installer_wraps_llm_and_restores_it_after():
    original = FakeResource()
    resources = RuntimeResources(llm=original)  # type: ignore[arg-type]  # fake, not a real provider
    installer = ResourceFaultInstaller(
        resources, [ResourceFault("llm", RuntimeError("boom"))]
    )

    with installer:
        assert resources.llm is not original
        with pytest.raises(RuntimeError):
            resources.llm.sync_method(1)  # type: ignore[attr-defined]

    assert resources.llm is original


def test_installer_restores_even_if_the_body_raises():
    original = FakeResource()
    resources = RuntimeResources(llm=original)  # type: ignore[arg-type]  # fake, not a real provider
    installer = ResourceFaultInstaller(
        resources, [ResourceFault("llm", RuntimeError("boom"))]
    )

    with pytest.raises(ValueError):  # noqa: SIM117 - want the with inside the raises
        with installer:
            raise ValueError("unrelated failure inside the run")

    assert resources.llm is original


def test_installer_can_wrap_an_additional_named_resource():
    fake = FakeResource()
    resources = RuntimeResources()
    resources.set("catalog", fake)
    installer = ResourceFaultInstaller(
        resources, [ResourceFault("catalog", RuntimeError("boom"))]
    )

    with installer, pytest.raises(RuntimeError):
        resources.get("catalog").sync_method(1)

    assert resources.get("catalog") is fake


def test_installer_rejects_an_unknown_resource_name():
    resources = RuntimeResources()
    installer = ResourceFaultInstaller(
        resources, [ResourceFault("no_such_thing", RuntimeError())]
    )

    with pytest.raises(ScenarioError, match="no such resource"), installer:
        pass


def test_installer_rejects_a_resource_that_is_none():
    resources = RuntimeResources()  # llm defaults to None
    installer = ResourceFaultInstaller(
        resources, [ResourceFault("llm", RuntimeError())]
    )

    with pytest.raises(ScenarioError, match="nothing to fail"), installer:
        pass


# --- ScenarioLab.fail_resource: end-to-end ------------------------------------ #


class Problem(BaseModel):
    text: str


@tool
async def kubectl(resource: str) -> str:
    """Query the state of k8s resources."""
    return f"status {resource}: ok"


class K8sAgent(Agent):
    name = "k8s"
    consumes = [Consume(Problem), Consume(ToolAnswer)]
    produces = [ToolUse(name="k8s", system="Use the kubectl tool.", tools=[kubectl])]


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request: LLMRequest):
        yield LLMResponse(text="")


def _resources() -> RuntimeResources:
    return RuntimeResources(
        llm=ScriptedLLM(['{"type":"answer","text":"pods: all good"}'])
    )


def test_fail_resource_llm_triggers_the_honest_fallback():
    """`ToolUse._loop` calls `structured_llm` (§59: swallows provider errors,
    returns `None`) — with the LLM resource always failing, the loop can't
    reach a decision and answers with its own honest fallback text instead
    of crashing or hanging.
    """
    lab = ScenarioLab([K8sAgent()], resources=_resources)
    lab.fail_resource("llm", ConnectionError("model unreachable"))

    result = run(lab.run(Problem(text="check pods")))

    answer = result.artifacts(ToolAnswer).exists()
    assert answer.text == "Could not reach a decision."
    result.errors.none()  # the agent didn't crash, it just got an honest fallback


def test_fail_resource_llm_recovers_once_times_is_exhausted():
    lab = ScenarioLab(
        [K8sAgent()],
        resources=lambda: RuntimeResources(
            llm=ScriptedLLM(['{"type":"answer","text":"pods: all good"}'])
        ),
    )
    lab.fail_resource("llm", ConnectionError("transient"), times=1)

    result = run(lab.run(Problem(text="check pods")))

    answer = result.artifacts(ToolAnswer).exists()
    assert answer.text == "pods: all good"  # structured_llm's own retry absorbed it
    result.errors.none()


def test_fail_resource_is_one_shot_and_restores_the_llm():
    lab = ScenarioLab([K8sAgent()], resources=_resources)
    lab.fail_resource("llm", ConnectionError("model unreachable"))

    run(lab.run(Problem(text="check pods")))
    # no re-queued fault -> the second run must not fault again
    result = run(
        lab.run(
            Problem(text="check pods again"),
            max_iterations=100,
        )
    )
    result.errors.none()


# --- fail_resource by typed key (type / ResourceKey) -------------------------- #

CATALOG_KEY: ResourceKey[FakeResource] = ResourceKey("scenario_catalog")


def test_installer_can_wrap_a_typed_resource():
    fake = FakeResource()
    resources = RuntimeResources()
    resources.register(CATALOG_KEY, fake)
    installer = ResourceFaultInstaller(
        resources, [ResourceFault(CATALOG_KEY, RuntimeError("boom"))]
    )

    with installer, pytest.raises(RuntimeError, match="boom"):
        resources.require(CATALOG_KEY).sync_method(1)

    assert resources.require(CATALOG_KEY) is fake


class Reply(BaseModel):
    text: str


@produce(Reply)
async def use_catalog(call):
    catalog = call.context.resources.require(CATALOG_KEY)
    try:
        text = catalog.sync_method(1)
    except Exception as exc:  # the injected fault surfaces as a handled failure
        text = f"fallback: {exc}"
    call.effects.create(Reply(text=text))
    return None


class CatalogAgent(Agent):
    name = "catalog"
    consumes = [Consume(Problem)]
    produces = [use_catalog]


def test_fail_resource_by_typed_key_end_to_end():
    resources = RuntimeResources(llm=ScriptedLLM([]))
    resources.register(CATALOG_KEY, FakeResource())

    lab = ScenarioLab([CatalogAgent()], resources=resources)
    lab.fail_resource(CATALOG_KEY, RuntimeError("catalog down"))

    result = run(lab.run(Problem(text="x")))

    assert "catalog down" in result.artifacts(Reply).exists().text
    result.errors.none()
    assert resources.require(CATALOG_KEY).sync_calls == []  # never reached the real one


def test_fail_resource_rejects_an_unregistered_typed_key():
    resources = RuntimeResources()
    lab = ScenarioLab([CatalogAgent()], resources=resources)
    lab.fail_resource(CATALOG_KEY, RuntimeError("boom"))

    with pytest.raises(ScenarioError, match="no such resource"):
        run(lab.run(Problem(text="x")))


def test_stub_resource_by_typed_key_returns_value_end_to_end():
    resources = RuntimeResources(llm=ScriptedLLM([]))
    real = FakeResource()
    resources.register(CATALOG_KEY, real)
    lab = ScenarioLab([CatalogAgent()], resources=resources)
    lab.stub_resource(CATALOG_KEY, returns="stubbed catalog")

    result = run(lab.run(Problem(text="x")))

    assert result.artifacts(Reply).exists().text == "stubbed catalog"
    result.errors.none()
    assert real.sync_calls == []  # never delegated to the real resource
