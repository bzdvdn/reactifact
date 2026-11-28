from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Protocol,
    TypeVar,
    cast,
    overload,
)

from .authz import Access, Authorizer, Principal
from .guardrails import GuardrailPolicy
from .providers import EmbeddingProvider, LLMProvider
from .quota import QuotaTracker
from .sources import Source

if TYPE_CHECKING:
    from .budget import Budget, BudgetTracker
    from .context_builder import ContextBuilder
    from .pricing import Pricer
    from .redaction import Redactor

T = TypeVar("T")

#: Mints an artifact id from its model/type name — injectable for deterministic
#: runs (`reactifact.replay.counter_ids`). `None` keeps the uuid default.
IdFactory = Callable[[str], str]


class MetricsSink(Protocol):
    """A metrics sink a produce can record domain counters/observations into.

    Satisfied structurally by `reactifact.metrics.Metrics` (no import needed
    here). `RuntimeResources.metrics` defaults to a no-op, so
    `call.context.resources.metrics.increment(...)` is always safe.
    """

    def increment(self, name: str, value: float = 1.0, **labels: Any) -> None: ...
    def observe(self, name: str, value: float, **labels: Any) -> None: ...


class _NullMetrics:
    """No-op `MetricsSink` (the default when none is configured)."""

    def increment(self, name: str, value: float = 1.0, **labels: Any) -> None:
        pass

    def observe(self, name: str, value: float, **labels: Any) -> None:
        pass


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
        record_tool_args: bool = False,
        id_factory: IdFactory | None = None,
        pricer: Pricer | None = None,
        metrics: MetricsSink | None = None,
        principal: Principal | None = None,
        authorizer: Authorizer | None = None,
        guardrails: GuardrailPolicy | None = None,
        quota: QuotaTracker | None = None,
        tool_recorder: Any | None = None,
        **additional: Any,
    ):
        self.llm = llm
        self.embedder = embedder
        self.sources = sources or {}
        # A domain metrics sink (`reactifact.metrics.Metrics` satisfies the
        # protocol). Defaults to a no-op, so a produce can always call
        # `call.context.resources.metrics.increment("my_domain_total", ...)`.
        self.metrics: MetricsSink = metrics if metrics is not None else _NullMetrics()
        # Who this run acts for, and the policy that gates what it may do
        # (`reactifact.authz`). Both `None` by default: with no authorizer every
        # action is allowed, so authz is opt-in and non-breaking. Enforced by
        # the runtime on agent runs and artifact writes, by the tool loop on
        # tool execution, and available to produces as
        # `resources.require_authorized(action, resource)`.
        self.principal = principal
        self.authorizer = authorizer
        # Artifact-boundary guardrails (`reactifact.guardrails`), applied to
        # every agent-produced Create/Update before it is committed. `None`
        # disables them.
        self.guardrails = guardrails
        # Cross-turn, per-principal usage limits (`reactifact.quota`). `None`
        # disables quota enforcement.
        self.quota = quota
        #: Optional record/replay wrapper for framework tool calls
        #: (`execute_tool`): `RecordingToolRecorder` / `ReplayToolRecorder`.
        self.tool_recorder = tool_recorder
        # Applies `Budget.max_cost`: `(model, prompt_tokens, completion_tokens)
        # -> cost`. `None` (default) makes a cost limit inert — the framework
        # ships no built-in prices on purpose (see `reactifact.pricing`).
        self.pricer = pricer
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
        # Opt-in: record tool-call arguments on `ToolRun.args` (scrubbed by
        # `redactor` when set). Off by default because args may carry secrets.
        self.record_tool_args = record_tool_args
        # Framework-wide pass/fail cutoff for `Verify` (verify.py): `None`
        # means "use Verify's own DEFAULT_THRESHOLD". A `Verify` instance's
        # own explicit `threshold=` still overrides this per agent.
        if (
            verification_threshold is not None
            and not 0.0 <= verification_threshold <= 1.0
        ):
            raise ValueError("verification_threshold must be between 0 and 1")
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
        #: Typed resources registered with `owned=True`: `aclose()` closes them
        #: (LIFO, before llm/embedder). Shared/app-owned resources stay out.
        self._owned: list[Any] = []
        self.additional = additional
        # Set by Runtime per turn on a shallow copy of this instance (not a
        # constructor param — the runtime, not the caller, owns these): the
        # active Budget and its wall-clock deadline, read back by ToolUse's own
        # inner loop (§ tools/use) to enforce the tool-call/time budget
        # between its own round-trips, not just at the top-level
        # Runtime._budget_exhausted check. `budget_tracker` is the per-turn
        # token/cost counters (set only when the budget tracks tokens/cost),
        # read the same way. The copy keeps a resources instance shared across
        # sessions/turns from being mutated by concurrent turns.
        self.budget: Budget | None = None
        self.budget_deadline: float | None = None
        self.budget_tracker: BudgetTracker | None = None
        #: Current turn's run id / trace scope / tracer, set by `Runtime` on the
        #: per-turn copy. Subflows read them to keep a child run linked to the
        #: parent (trace metadata `parent_run_id`) and to share the tracer.
        self.run_id: str = ""
        self.trace_scope: str = ""
        self.tracer: Any | None = None

    def get_source(self, source_id: str) -> Source | None:
        return self.sources.get(source_id)

    # ---- authorization (#57) ---------------------------------------------- #

    def authorize(self, action: str, resource: str) -> bool:
        """Whether the current principal may `action` on `resource`.

        `True` when no authorizer is configured (authz is opt-in).
        """
        if self.authorizer is None:
            return True
        return self.authorizer.authorize(self.principal, action, resource)

    @property
    def access(self) -> Access:
        """A focused, injectable view over `authorize`/`require_authorized`."""
        return Access(self.authorize, self.principal)

    def require_authorized(self, action: str, resource: str) -> None:
        """`authorize` or raise `AuthorizationError` — the produce-side gate."""
        self.access.require(action, resource)

    def quota_key(self) -> str:
        """The key a `QuotaTracker` counts against: the principal, else the run."""
        if self.principal is not None:
            return self.principal.id
        return "default"

    # ---- typed resources (#2) --------------------------------------------- #

    def resolve(self, annotation: type[T]) -> T | None:
        """Resolve an injected collaborator for a step parameter.

        Typed registrations win, then the built-in resource attributes
        (`llm`, `embedder`, `redactor`, …) when they match the annotation.
        Returns `None` when nothing matches, so the caller can try artifacts
        next or raise a useful missing-input error.
        """
        if annotation is RuntimeResources:
            return cast("T", self)
        if annotation in self._typed:
            return cast("T", self._typed[annotation])
        if annotation is Access:
            return cast("T", self.access)
        for name in (
            "llm",
            "embedder",
            "context_builder",
            "redactor",
            "pricer",
            "metrics",
            "principal",
            "authorizer",
            "guardrails",
            "quota",
            "tool_recorder",
        ):
            value = getattr(self, name, None)
            if value is not None and isinstance(value, annotation):
                return value
        return None

    def with_overrides(self, overrides: Mapping[type[Any], Any]) -> RuntimeResources:
        """A shallow copy with typed overrides (used by `Pipeline.override`)."""
        clone: RuntimeResources = copy.copy(self)
        clone._typed = dict(self._typed)
        clone._typed.update(overrides)
        clone.additional = dict(self.additional)
        return clone

    def register(
        self,
        key: type[T] | ResourceKey[T],
        instance: T,
        *,
        owned: bool = False,
    ) -> T:
        """Attaches `instance` under `key` (its type, or a `ResourceKey`).

        Returns the instance, so `store = resources.register(Store, Store(...))`
        reads as a one-liner. Re-registering a key overwrites it.

        `owned=True` hands the instance to this `RuntimeResources`: `aclose()`
        closes it (LIFO, before `llm`/`embedder`) via `aclose()` or a sync
        `close()`. Use it for clients/settings created *with* these resources —
        in particular in a per-turn factory, where `ChatAssistant` closes the
        resources after every turn. Leave it off for long-lived shared clients
        (a DB pool, a store) registered into per-turn resources: those are
        app-owned and must be closed in your lifespan, not per turn.
        """
        self._typed[key] = instance
        if owned:
            self._owned.append(instance)
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
        """Closes owned resources, then the llm/embedder clients.

        Attach a resource with `register(..., owned=True)` to hand its lifetime
        to this instance; owned resources close in reverse registration order
        (dependencies first), then `embedder`, then `llm`. Closing is
        duck-typed: an async `aclose()` or a sync `close()`.

        Nothing in the runtime calls this automatically — resources are
        typically shared across many turns/runtimes, and closing them early
        would break whatever still holds a reference. Call it yourself once, at
        real shutdown: a FastAPI `lifespan`, `RuntimeResources.scope(...)`, or
        the end of a script. `ChatAssistant` is the one exception: for a
        callable `resources=` it closes the fresh per-turn instance after every
        turn, so `owned=True` there closes the clients created by that factory.

        Best-effort: every closer is attempted; failures are collected into an
        `ExceptionGroup` afterwards. Owned resources are cleared even if one
        fails, so a retry does not close them twice.
        """
        errors: list[Exception] = []
        seen: set[int] = set()

        async def _close(value: Any) -> None:
            if value is None or id(value) in seen:
                return
            seen.add(id(value))
            closer = getattr(value, "aclose", None)
            if not callable(closer):
                closer = getattr(value, "close", None)
            if not callable(closer):
                return
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 — keep closing the rest
                errors.append(exc)

        owned, self._owned = self._owned, []
        for value in reversed(owned):
            await _close(value)
        await _close(self.embedder)
        await _close(self.llm)
        if errors:
            raise ExceptionGroup("reactifact: failed to close resources", errors)

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
