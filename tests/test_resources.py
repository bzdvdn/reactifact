import asyncio

from reactifact.context import Context
from reactifact.providers import FakeEmbedder, FakeLLM
from reactifact.resources import ResourceKey, RuntimeResources


def test_runtime_resources_init():
    resources = RuntimeResources()
    assert resources.llm is None
    assert resources.embedder is None


def test_runtime_resources_custom():
    llm = FakeLLM()
    embedder = FakeEmbedder()
    resources = RuntimeResources(llm=llm, embedder=embedder)
    assert resources.llm is llm
    assert resources.embedder is embedder


def test_runtime_resources_additional():
    resources = RuntimeResources()
    resources.set("custom", 42)
    assert resources.get("custom") == 42


def test_context_resources():
    llm = FakeLLM()
    resources = RuntimeResources(llm=llm)
    ws = Context(resources=resources)
    assert ws.resources.llm is llm


class _SpyLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _SpyEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_aclose_closes_llm_and_embedder_when_present():
    llm = _SpyLLM()
    embedder = _SpyEmbedder()
    resources = RuntimeResources(llm=llm, embedder=embedder)
    asyncio.run(resources.aclose())
    assert llm.closed is True
    assert embedder.closed is True


class _Store:
    pass


class _Other:
    pass


def test_typed_register_get_require_has():
    import pytest

    resources = RuntimeResources()
    store = resources.register(_Store, _Store())

    assert resources.get(_Store) is store
    assert resources.require(_Store) is store
    assert resources.has(_Store) is True
    assert resources.has(_Other) is False
    assert _Store in resources.registered

    with pytest.raises(LookupError, match="_Other"):
        resources.require(_Other)


def test_resource_key_for_two_instances_of_one_type():
    primary = ResourceKey[_Store]("primary")
    replica = ResourceKey[_Store]("replica")
    resources = RuntimeResources()
    a, b = _Store(), _Store()
    resources.register(primary, a)
    resources.register(replica, b)

    assert resources.require(primary) is a
    assert resources.require(replica) is b
    assert resources.get("nope") is None  # string keys still hit additional


def test_resource_scope_builds_and_closes_per_loop():
    closed = {"n": 0}

    class Spy(_SpyLLM):
        async def aclose(self) -> None:
            closed["n"] += 1

    async def scenario() -> None:
        async with RuntimeResources.scope(
            lambda: RuntimeResources(llm=Spy())
        ) as resources:
            assert resources.llm is not None
        assert closed["n"] == 1

    asyncio.run(scenario())


def test_resource_scope_accepts_async_factory():
    async def build() -> RuntimeResources:
        return RuntimeResources(llm=FakeLLM())

    async def scenario() -> None:
        async with RuntimeResources.scope(build) as resources:
            assert resources.llm is not None

    asyncio.run(scenario())


def test_aclose_is_a_noop_without_aclose_support():
    """FakeLLM/FakeEmbedder (and None) don't define aclose — duck-typed skip,
    not an AttributeError."""
    resources = RuntimeResources(llm=FakeLLM(), embedder=FakeEmbedder())
    asyncio.run(resources.aclose())  # must not raise

    empty = RuntimeResources()
    asyncio.run(empty.aclose())  # must not raise
