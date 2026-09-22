from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload

from .providers import EmbeddingProvider, LLMProvider
from .sources import Source

if TYPE_CHECKING:
    from .budget import Budget
    from .context_builder import ContextBuilder
    from .redaction import Redactor

T = TypeVar("T")

#: Mints an artifact id from its model/type name — injectable for deterministic
#: runs (`reactifact.replay.counter_ids`). `None` keeps the uuid default.
IdFactory = Callable[[str], str]


class ResourceKey(Generic[T]):
    """A typed key for registering a resource when the *type* isn't a good key.

    `register(MyStore, store)` keys by `MyStore`, which is all most apps need.
    Use a `ResourceKey` when you have two of the same type (two `Store`s, a
    primary and a replica) or want an app-specific handle:

        PRIMARY = ResourceKey[Store]("primary")
        resources.register(PRIMARY, store)
        ...
        store = resources.require(PRIMARY)
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"ResourceKey({self.name!r})"


class RuntimeResources:
    def __init__(
        self,
        llm: LLMProvider | None = None,
        embedder: EmbeddingProvider | None = None,
        sources: dict[str, Source] | None = None,
        context_builder: ContextBuilder | None = None,
        verification_threshold: float | None = None,
        redactor: Redactor | None = None,
        id_factory: IdFactory | None = None,
        **additional: Any,
    ):
        self.llm = llm
        self.embedder = embedder
        self.sources = sources or {}
        # Injected id source for artifacts created without an explicit id
        # (`None` = the uuid default). A deterministic factory
        # (`reactifact.replay.counter_ids`) makes an unmodified app's
        # `context_hash` reproducible run to run — see `reactifact.replay.verify_run`.
        self.id_factory = id_factory
        # Applied to trace text only (artifact `data`, LLM messages/responses,
        # errors) before it reaches a sink — never to the live `Context` or a
        # persisted session. `None` (default) reproduces the pre-hook behavior.
        # See `reactifact.redaction`.
        self.redactor = redactor
        # Framework-wide pass/fail cutoff for `Verify` (verify.py): `None`
        # means "use Verify's own DEFAULT_THRESHOLD". A `Verify` instance's
        # own explicit `threshold=` still overrides this per agent.
        self.verification_threshold = verification_threshold
        # Runtime-level policy for what actually goes into an agent's inputs
        # (ranking/truncation) — `None` reproduces the old, unranked
        # behavior. See `context_builder.py`; applied inside
        # `Agent._collect_inputs` (`agents.py`), so both the runtime's
        # provenance (`Runtime._collect_reads`) and the agent's actual
        # produce inputs go through the same builder call and stay in sync.
        self.context_builder = context_builder
        # Typed resources: app collaborators (a knowledge store, decision
        # tools, a settings object) registered against their type or a
        # `ResourceKey`, so a produce reads `resources.require(Store)` and gets
        # `Store` — no `or None`, no duck-typing, no error surfacing three calls
        # later. `additional` stays as the string-keyed escape hatch.
        self._typed: dict[Any, Any] = {}
        self.additional = additional
        # Set by Runtime per turn (not a constructor param — the runtime, not
        # the caller, owns these): the active Budget and its wall-clock
        # deadline, read back by ToolUse's own inner loop (§ tool_use.py) to
        # enforce the tool-call/time budget between its own round-trips, not
        # just at the top-level Runtime._budget_exhausted check.
        self.budget: Budget | None = None
        self.budget_deadline: float | None = None

    def get_source(self, source_id: str) -> Source | None:
        return self.sources.get(source_id)

    # ---- typed resources (#2) --------------------------------------------- #

    def register(self, key: type[T] | ResourceKey[T], instance: T) -> T:
        """Attaches `instance` under `key` (its type, or a `ResourceKey`).

        Returns the instance, so `store = resources.register(Store, Store(...))`
        reads as a one-liner. Re-registering a key overwrites it.
        """
        self._typed[key] = instance
        return instance

    @overload
    def get(self, key: str) -> Any: ...
    @overload
    def get(self, key: type[T]) -> T | None: ...
    @overload
    def get(self, key: ResourceKey[T]) -> T | None: ...
    def get(self, key: str | type[T] | ResourceKey[T]) -> Any:
        """A string key reads `additional`; a type/`ResourceKey` reads typed."""
        if isinstance(key, str):
            return self.additional.get(key)
        return self._typed.get(key)

    def require(self, key: type[T] | ResourceKey[T]) -> T:
        """Like `get`, but a missing resource is a loud, early `LookupError`.

        Reach for this inside a produce/agent for a resource the app *must*
        have configured — the failure names the missing type instead of being a
        `None` that blows up later.
        """
        value = self._typed.get(key)
        if value is None:
            raise LookupError(
                f"resource {key!r} is not registered on RuntimeResources; "
                f"call resources.register({key!r}, ...) when building them"
            )
        return cast("T", value)

    def has(self, key: type[T] | ResourceKey[T]) -> bool:
        """Whether a typed resource is registered (`is_configured`, explicitly)."""
        return key in self._typed

    @property
    def registered(self) -> frozenset[Any]:
        """The keys of every registered typed resource (for diagnostics)."""
        return frozenset(self._typed)

    def typed_values(self) -> list[Any]:
        """Every registered typed resource value (read-only introspection).

        `registered` gives the keys; this gives the values — e.g. so a generic
        tool scanner (`reactifact.testing.fault`) can find a `list[Tool]`
        registered with `register(...)`, not just one stashed via `set(...)`.
        """
        return list(self._typed.values())

    def set(self, name: str, value: Any) -> None:
        self.additional[name] = value

    async def aclose(self) -> None:
        """Closes the llm/embedder clients if they support it.

        Duck-typed: `LLMProvider`/`EmbeddingProvider` don't require `aclose`
        (a fake/no-op test double doesn't need one), so it's called only when
        present. Nothing in the runtime calls this automatically — resources
        are typically shared across many turns/runtimes, and closing them
        early would break whatever still holds a reference. Call it yourself
        once, at real shutdown: a FastAPI `lifespan`, or the end of a script.
        `ChatAssistant` is the one exception — see its docstring.
        """
        for provider in (self.llm, self.embedder):
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    @classmethod
    def scope(
        cls, factory: Callable[[], RuntimeResources | Awaitable[RuntimeResources]]
    ) -> ResourceScope:
        """`async with RuntimeResources.scope(build) as resources:` — see `ResourceScope`."""
        return ResourceScope(factory)


class ResourceScope(AbstractAsyncContextManager["RuntimeResources"]):
    """Builds `RuntimeResources` on entry and closes them on exit — loop-safe.

    Providers (httpx, a vector DB, …) bind their clients to the event loop they
    were created on. A resource built once per process and reused across loops
    — a CLI, or pytest giving each test its own loop — eventually raises
    `RuntimeError: Event loop is closed`. Build resources *inside* the scope so
    they live and die with one loop:

        async with RuntimeResources.scope(build_resources) as resources:
            runtime = Runtime(Context(resources=resources), agents=[...])
            await runtime.arun(request={"user": "bob"})

    The factory may be sync or async. `resources.aclose()` runs on exit (and on
    an exception inside the block), so the HTTP clients don't leak.
    """

    def __init__(
        self,
        factory: Callable[[], RuntimeResources | Awaitable[RuntimeResources]],
    ):
        self._factory = factory
        self.resources: RuntimeResources | None = None

    async def __aenter__(self) -> RuntimeResources:
        built = self._factory()
        self.resources = await built if inspect.isawaitable(built) else built
        return self.resources

    async def __aexit__(self, *exc: object) -> None:
        if self.resources is not None:
            await self.resources.aclose()
