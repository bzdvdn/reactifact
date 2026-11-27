"""reactifact.chat — the app-facing chat layer (sessions + turns + history).

A thin, transport-agnostic layer on top of the runtime: it owns sessions
(`SessionStore`), the wire-neutral turn loop ("create the user artifact, stream
status events, then the terminal reply") and history reconstruction. It knows
nothing about HTTP/SSE — the web adapter lives in `reactifact.web`.

Two levels of use:

- `ChatAssistant` — concrete, batteries-included for the canonical chat
  contract. Configure it with hooks (agents, `user_message` model, `reply`).
- `run_message` / `default_session_state` — building blocks, for apps whose
  loop or transport differs (bots, custom SSE, medic-lab-style steering).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from .artifacts import Artifact
from .budget import Budget
from .consume import Consume
from .context import Context
from .events import Event
from .request import reset_request, set_request
from .resources import RuntimeResources
from .runtime import Dispatcher, DispatchErrorHandler, Runtime
from .session import Session, SessionStore
from .step import Step, StepGroup, create_step_group
from .step.group import normalize_units

if TYPE_CHECKING:
    from .contracts.report import ContractReport
    from .validate import ValidationReport

logger = logging.getLogger("reactifact.chat")


#: The three frame kinds of the canonical chat (start / progress / terminal).
ChatEventKind = Literal["session", "status", "message"]


class ChatEvent(BaseModel):
    """One frame of a chat turn, transport-neutral.

    `kind` is the wire contract of the canonical chat:
    ``session`` (start), ``status`` (progress announcement) or ``message``
    (terminal reply). It is a closed `Literal`, so the schema is
    introspectable (`ChatEvent.model_json_schema()`) and a typo is a type
    error, not a frame a client silently ignores. To *rename* these on the
    wire for a client that speaks a different vocabulary, configure
    `reactifact.web.create_chat_router(event_names=…)` — don't fork the
    router.
    """

    kind: ChatEventKind
    session_id: str = ""
    message: str = ""
    waiting: bool = False
    payload: dict[str, Any] = {}


# --- building blocks ------------------------------------------------------- #


def _fallback_payload(fallback_reply: str) -> dict[str, Any]:
    """The honest terminal payload when a turn crashed (§59)."""
    return {"reply": fallback_reply, "waiting": False, "error": True}


@dataclass(frozen=True)
class ChatReply:
    """A declarative terminal-reply projection for `Chat`/`ChatAssistant`.

    The common chat shape is "find the answer for this turn, render it, attach
    sources/metadata". `ChatReply` is that shape without a hand-written
    `reply(ctx, msg_id)` function:

        reply = ChatReply(
            Answer,
            render=lambda a: mask(a.text),
            extra=lambda artifact: {"sources": artifact.data.sources},
        )
        chat = Chat(..., reply=reply)

    `field` is the model field matched against the turn's message id (default:
    `query_id`). The latest matching artifact wins. `extra` receives the
    artifact, so it can attach ids, sources or domain metadata; `empty`
    overrides the honest fallback payload. A plain callable still works
    unchanged.
    """

    type: type[BaseModel]
    field: str = "query_id"
    render: Callable[[Any], str] | None = None
    extra: Callable[[Artifact[Any]], Mapping[str, Any]] | None = None
    empty: Mapping[str, Any] | None = None

    def build(self, context: Context, msg_id: str) -> dict[str, Any]:
        candidates = [
            artifact
            for artifact in context.list_artifacts(self.type)
            if getattr(artifact.data, self.field, None) == msg_id
        ]
        if not candidates:
            return dict(
                self.empty or {"reply": "No reply assembled.", "waiting": False}
            )
        artifact = max(candidates, key=lambda item: item.created_at)
        text = (
            self.render(artifact.data)
            if self.render is not None
            else str(getattr(artifact.data, "text", ""))
        )
        payload: dict[str, Any] = {"reply": text, "waiting": False}
        if self.extra is not None:
            payload.update(dict(self.extra(artifact)))
        return payload


async def run_message(
    runtime: Runtime,
    text: str,
    *,
    user_message: type[BaseModel],
    reply: Callable[[Context, str], dict[str, Any]] | ChatReply,
    session_id: str = "",
    create_message: Callable[..., str] | None = None,
    status_kinds: Sequence[str] = ("status",),
    fallback_reply: str = "No reply assembled.",
    request: Mapping[str, Any] | None = None,
) -> AsyncIterator[ChatEvent]:
    """Run one user turn: create the input artifact, stream status events,
    emit the terminal reply.

    `reply(ctx, msg_id) -> dict` adapts the app's state into the `message`
    payload (the only domain hook). Statuses are deduplicated so repeated
    progress announcements don't double-emit.

    `create_message(ctx, text) -> msg_id` overrides how the turn enters the
    context (default: create a `user_message` artifact) — HITL apps where a
    new turn resumes a pending question instead of appending a message
    (devops-style clarify) pass their own. Also accepts the 3-arg shape
    `create_message(ctx, text, session_id) -> msg_id` for a hook that needs
    the current turn's `session_id` (e.g. to stamp it onto the created
    artifact) and the 4-arg shape `create_message(ctx, text, session_id,
    request) -> msg_id` for per-request metadata (a category, tenant, user)
    — detected from the callable's own arity, so the 2-arg shape keeps
    working unchanged. During the hook `ctx.request` is the same mapping
    passed as `request=`, so a 2/3-arg hook can read it too without a
    `ContextVar` of its own.

    `status_kinds` selects which progress event kinds are forwarded as `status`
    frames (default: only `status`; e.g. tool-announcing demos also forward
    `agent`).

    Errors never escape: a failed runtime/reply degrades to the `fallback_reply`
    `message` event (logged via `reactifact.chat` logger) so a web layer never
    delivers a 500 mid-stream.
    """
    forwarded = set(status_kinds)
    ctx = runtime.context
    try:
        token = set_request(request) if request is not None else None
        try:
            if create_message is not None:
                msg_id = _call_create_message(
                    create_message, ctx, text, session_id, request
                )
            else:
                msg_id = ctx.create(user_message(text=text, session_id=session_id)).id
        finally:
            if token is not None:
                reset_request(token)
    except Exception:
        logger.exception("chat.run_message: failed to enter the turn")
        yield ChatEvent(
            kind="message",
            session_id=session_id,
            payload=_fallback_payload(fallback_reply),
        )
        return

    last: str | None = None
    try:
        async for event in runtime.astream(request=request):
            if event.kind not in forwarded:
                continue
            if event.message == last:
                continue
            last = event.message
            yield ChatEvent(kind="status", session_id=session_id, message=event.message)
    except Exception:
        # The runtime crashed — the context may be half-applied. Skip the reply
        # hook (it could echo stale state) and degregate to the honest fallback.
        logger.exception("chat.run_message: runtime crashed inside the turn")
        yield ChatEvent(
            kind="message",
            session_id=session_id,
            payload=_fallback_payload(fallback_reply),
        )
        return
    try:
        payload = (
            reply.build(ctx, msg_id)
            if isinstance(reply, ChatReply)
            else reply(ctx, msg_id)
        )
    except Exception:
        logger.exception("chat.run_message: reply hook crashed")
        payload = _fallback_payload(fallback_reply)
    yield ChatEvent(
        kind="message",
        session_id=session_id,
        payload=payload or _fallback_payload(fallback_reply),
    )


def default_session_state(
    ctx: Context, *, user_message: type[BaseModel]
) -> dict[str, Any]:
    """Generic history: every artifact with a `text` field, in creation order.

    Artifacts of `user_message`'s type are marked `user`, everything else —
    `assistant`. Apps with richer reply payloads pass their own hook.
    """
    messages: list[dict[str, Any]] = []
    # Insertion order is the true creation order. Do not sort by `created_at`:
    # append-only session replay reconstructs committed artifacts at load time,
    # so their timestamps are newer than the working-tree user messages and a
    # timestamp sort would push all user turns to the front.
    for artifact in ctx.list_artifacts():
        data = artifact.data
        text = getattr(data, "text", None)
        if not isinstance(text, str):
            continue
        role = "user" if isinstance(data, user_message) else "assistant"
        messages.append(
            {
                "role": role,
                "text": text,
                "at": artifact.created_at.isoformat(),
            }
        )
    pending = ctx.latest_pending_question()
    if pending is not None:
        data = pending.data
        messages.append(
            {
                "role": "assistant",
                "text": data.question,
                "waiting": True,
                "question": {
                    "id": pending.id,
                    "kind": data.kind,
                    "question": data.question,
                    "schema": data.resolution_schema,
                    "notes": dict(data.notes),
                },
                "at": pending.created_at.isoformat(),
            }
        )
    return {"messages": messages}


def _resolve(value: Any) -> Any:
    return value() if callable(value) else value


def _accepts_arg(func: Callable[..., Any], count: int) -> bool:
    """Whether `func` (already known callable) declares at least `count`
    positional-or-keyword parameters — used to detect the opt-in, per-request
    call shapes below without breaking the plain zero/two-arg factories that
    predate them. A callable whose signature can't be inspected (a builtin, a
    C extension) is assumed to be the old, arg-less shape."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return len(params) >= count


def _resolve_with_session(
    value: Any,
    session_id: str,
    request: Mapping[str, Any] | None = None,
) -> Any:
    """Like `_resolve`, but passes `session_id` (and optionally `request`) when
    the factory declares the extra parameters — the per-request resource shape
    (`resources=lambda session_id, request: build_resources(...)`) alongside
    the pre-existing one-arg (`resources=lambda session_id: ...`) and zero-arg
    forms, which keep working unchanged. `ChatAssistant` already knows the
    session and turn request at this point; a factory can opt into either.
    """
    if not callable(value):
        return value
    if _accepts_arg(value, 2):
        return value(session_id, request or {})
    return value(session_id) if _accepts_arg(value, 1) else value()


def _call_create_message(
    create_message: Callable[..., str],
    ctx: Context,
    text: str,
    session_id: str,
    request: Mapping[str, Any] | None = None,
) -> str:
    """Calls `create_message` with the widest shape it declares.

    Four params get `(ctx, text, session_id, request)`, three get
    `(ctx, text, session_id)`, and the pre-existing two-arg shape gets
    `(ctx, text)`. Detected from arity so old hooks keep working unchanged.
    """
    if _accepts_arg(create_message, 4):
        return create_message(ctx, text, session_id, request)
    if _accepts_arg(create_message, 3):
        return create_message(ctx, text, session_id)
    return create_message(ctx, text)


# --- the canonical chat assistant ----------------------------------------- #


def _digest_build(
    summary_type: type[BaseModel], build: Callable[[str], BaseModel] | None
) -> Callable[[str], BaseModel]:
    """How `ChatMemory` turns digest text into the `summary_type` artifact.

    Uses the explicit `build` when given, else `summary_type(text=…)` if the
    model has a `text` field — otherwise it's a configuration error.
    """
    if build is not None:
        return build
    if "text" not in set(getattr(summary_type, "model_fields", {})):
        raise ValueError(
            f"{summary_type.__name__} has no `text` field; pass "
            "ChatMemory.build_summary"
        )

    def _build(text: str) -> BaseModel:
        return summary_type(text=text)

    return _build


@dataclass
class ChatMemory:
    """Bounded multi-turn memory policy for `ChatAssistant` (optional).

    Each field is independent; set at least one.

    - `keep`: raw `message_type` artifacts beyond the last N are pruned
      (`WindowPruner`).
    - `summarize` + `summary_type`: instead of dropping the pruned messages,
      fold them into one growing digest (`RollingDigestSummarizer`) — the
      callback is `(ctx, previous_digest_text, stale_artifacts) -> str | None`.
      `build_summary` builds the digest artifact from that text; it defaults to
      `summary_type(text=…)` when the model has a `text` field.
    - `compact_commits`: after each turn, `Context.compact(keep_commits=…)`
      bounds the commit log (the history, not the working artifacts). This is
      the safe choice when messages are correlated/typed rather than a plain
      role/text log (e.g. a `query_id`-linked user/assistant pair): it never
      removes an artifact, so it can't desync the transcript.

    `message_type` (needed for `keep`/`summarize`) is any model with
    `role`/`text`; for compaction-only it isn't needed at all, so
    `ChatMemory(compact_commits=…)` is a complete policy on its own.

    `message_types` (or a sequence passed as `message_type`) bounds a
    heterogeneous conversation — the natural `UserQuery`/`Answer` pair, or a
    marker base like `ChatMessage` whose concrete types inherit from it. The
    memory agent wakes on every listed type (and, for a marker base, on its
    subclasses), so one window covers the whole thread.
    """

    message_type: type[BaseModel] | Sequence[type[BaseModel]] | None = None
    message_types: Sequence[type[BaseModel]] | None = None
    keep: int | None = None
    summarize: Callable[[Context, str, list[Any]], Awaitable[str | None]] | None = None
    summary_type: type[BaseModel] | None = None
    build_summary: Callable[[str], BaseModel] | None = None
    digest_id: str = "digest"
    compact_commits: int | None = None

    @classmethod
    def from_transcript(cls, transcript: Any, **kwargs: Any) -> ChatMemory:
        """Builds a policy bound to every type in a `Transcript`.

        `ChatMemory.from_transcript(transcript, keep=..., summarize=...,
        summary_type=...)` removes the hand-maintained `message_type=`
        duplication; `Transcript.types` is the source of truth.
        """
        return cls(message_types=tuple(transcript.types), **kwargs)

    def resolved_message_types(self) -> tuple[type[BaseModel], ...]:
        """The message types to bound, normalized from the two fields."""
        if self.message_types is not None:
            types = tuple(self.message_types)
        elif self.message_type is None:
            return ()
        elif isinstance(self.message_type, type):
            types = (self.message_type,)
        else:
            types = tuple(self.message_type)
        if not types:
            raise ValueError("message_type must name at least one artifact type")
        return types


class ChatAssistant:
    """Session-persisted chat over the runtime, for the canonical contract.

    Configure with hooks — the assistant owns sessions, the turn loop and
    history. `groups`/`resources` accept values or callables (resolved per
    request, so a fresh `RuntimeResources` can replace providers).

    A callable `resources=` is assumed to build a fresh, turn-scoped
    `RuntimeResources` each call (e.g. `resources=lambda: build_resources()`)
    — `stream()` closes it (`RuntimeResources.aclose()`) after every turn, so
    its provider's HTTP client doesn't leak. Pass a plain `RuntimeResources`
    instance instead when you want one shared, long-lived provider across
    turns/sessions — that instance is never closed automatically; close it
    yourself at real shutdown. It is safe to share: the runtime installs
    per-turn budget/tracing wrappers on a shallow copy, so concurrent turns do
    not mutate the shared instance.

    `session_save_policy=` passes straight through to `Runtime` — `"per_turn"`
    trades finer crash-resilience granularity (a save at every generation
    boundary, the default) for one `session.save()` per turn, worthwhile once a
    multi-stage pipeline routinely produces several generations per turn (see
    `Runtime.__init__`'s own docstring for the trade-off).

    `dispatcher=` (and `on_dispatch_error=`) also passes straight through to
    `Runtime`: give an agent that uses `outbox.act(...)` a way to actually send
    its outbound side effects from a chat turn, with the same once-per-stable-id
    delivery `Runtime(dispatcher=...)` provides. Without it, a chat turn that
    records a `PendingAction` leaves it `pending` (the runtime warns) for someone
    to drain later.

    `resources=`/`create_message=` may optionally take the current turn's
    `session_id` — `resources=lambda session_id: build_resources(session_id)`
    (e.g. to attach an authenticated user looked up from the session) and
    `create_message=lambda ctx, text, session_id: ...` — detected from each
    callable's own arity, so the pre-existing zero/two-arg shapes keep
    working unchanged. Without this, per-request data (who's asking, not
    just what they asked) has no way into a turn short of a contextvar/
    side-channel set by the caller before `stream()`/`invoke()` — `resources=`
    and `create_message=` were otherwise the only two hooks in this class
    that never saw it.

    Base usage:

        assistant = ChatAssistant(
            store=store,
            groups=ALL_AGENTS,
            user_message=UserQuery,
            reply=knowledge_reply,
            resources=lambda: build_resources(),
        )
        async for ev in assistant.stream("hello", session_id="s1"):
            ...
    """

    def __init__(
        self,
        *,
        store: SessionStore,
        groups: Sequence[StepGroup] | Callable[[], Sequence[StepGroup]] | None = None,
        steps: Sequence[Step] | None = None,
        user_message: type[BaseModel],
        reply: Callable[[Context, str], dict[str, Any]] | ChatReply,
        session_state: Callable[[Context], dict[str, Any]] | None = None,
        resources: RuntimeResources | Callable[..., RuntimeResources] | None = None,
        budget: Budget | None = None,
        max_concurrency: int | None = None,
        tracer: Any = None,
        create_message: Callable[..., str] | None = None,
        status_kinds: Sequence[str] = ("status",),
        fallback_reply: str = "No reply assembled.",
        isolate_errors: bool = False,
        on_agent_error: Callable[[StepGroup, Event, BaseException], None] | None = None,
        session_save_policy: Literal["per_commit", "per_turn"] = "per_commit",
        memory: ChatMemory | None = None,
        dispatcher: Dispatcher | None = None,
        on_dispatch_error: DispatchErrorHandler | None = None,
    ):
        provided = [value for value in (groups, steps) if value is not None]
        if len(provided) > 1:
            raise ValueError("Pass only one of steps= or groups=")
        if steps is not None:
            resolved: Sequence[StepGroup] | Callable[[], Sequence[StepGroup]] = (
                normalize_units(steps=list(steps))
            )
        elif groups is not None:
            resolved = groups
        else:
            raise TypeError("ChatAssistant requires steps= or groups=")
        self.store = store
        self._groups = resolved
        self._user_message = user_message
        self._reply = reply
        self._session_state = session_state
        self._resources = resources
        self._budget = budget
        self._max_concurrency = max_concurrency
        self._tracer = tracer
        self._create_message = create_message
        self._status_kinds = tuple(status_kinds)
        self._fallback_reply = fallback_reply
        self._isolate_errors = isolate_errors
        self._on_agent_error = on_agent_error
        self._session_save_policy: Literal["per_commit", "per_turn"] = (
            session_save_policy
        )
        self._memory = memory
        self._dispatcher = dispatcher
        self._on_dispatch_error = on_dispatch_error
        # Serializes concurrent turns on the *same* session_id (a double
        # submit, a client retry): without this, two overlapping stream()
        # calls both load the same starting state and the later save() wins,
        # silently dropping the other turn (§59: no silent data loss).
        # Different session_ids never block each other. Entries are removed
        # once uncontended (`_lock_refs` hits 0) so this stays bounded by
        # concurrently-active sessions, not by every session_id ever seen —
        # see `_locked_session`.
        self._locks_guard = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}

    @asynccontextmanager
    async def _locked_session(self, session_id: str) -> AsyncGenerator[None, None]:
        """Mutual exclusion per `session_id` for the turn's duration (§59).

        The lock is created on first use and dropped once nothing holds it
        (`_lock_refs` hits 0) — sized by concurrently-active sessions, not
        every session_id ever seen, so a long-lived server doesn't grow this
        dict without bound.
        """
        async with self._locks_guard:
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
            self._lock_refs[session_id] = self._lock_refs.get(session_id, 0) + 1
        async with lock:
            try:
                yield
            finally:
                async with self._locks_guard:
                    self._lock_refs[session_id] -= 1
                    if self._lock_refs[session_id] <= 0:
                        self._lock_refs.pop(session_id, None)
                        self._session_locks.pop(session_id, None)

    async def _open(
        self,
        session_id: str,
        request: Mapping[str, Any] | None = None,
        *,
        build_resources: bool = True,
    ) -> Session:
        resources: RuntimeResources | None = None
        if build_resources:
            resources = _resolve_with_session(self._resources, session_id, request)
        elif not callable(self._resources):
            # A shared instance is harmless to attach; a per-turn factory is
            # deliberately not invoked for a read-only open (history()).
            resources = self._resources
        try:
            return await self.store.open(session_id, resources=resources)
        except Exception:
            # A callable `resources=` built a fresh per-turn instance; if the
            # store fails to open the session nothing else will close it.
            if build_resources and callable(self._resources) and resources is not None:
                with suppress(Exception):
                    await resources.aclose()
            raise

    def _memory_agents(self) -> list[StepGroup]:
        """The bounded-memory agent implied by `memory=`, or none."""
        memory = self._memory
        if memory is None:
            return []
        from .recipes.memory import RollingDigestSummarizer, WindowPruner

        produce: Any
        if memory.summarize is not None:
            if memory.summary_type is None:
                raise ValueError(
                    "ChatMemory.summarize needs message_type and summary_type"
                )
            message_types = memory.resolved_message_types()
            if not message_types:
                raise ValueError(
                    "ChatMemory.summarize needs message_type and summary_type"
                )
            produce = RollingDigestSummarizer(
                message_type=message_types,
                artifact_type=memory.summary_type,
                summarize=memory.summarize,
                build=_digest_build(memory.summary_type, memory.build_summary),
                window=memory.keep if memory.keep is not None else 8,
                digest_id=memory.digest_id,
            )
        elif memory.keep is not None:
            message_types = memory.resolved_message_types()
            if not message_types:
                raise ValueError("ChatMemory.keep needs message_type")
            produce = WindowPruner(message_types, keep=memory.keep)
        else:
            return []  # compaction-only: no message-level memory agent
        return [
            create_step_group(
                "memory",
                consumes=[Consume.by_base(t) for t in message_types],
                steps=[produce],
            )
        ]

    def _build_runtime(self, session: Session) -> Runtime:
        return Runtime(
            session.context,
            groups=[*_resolve(self._groups), *self._memory_agents()],
            session=session,
            budget=self._budget,
            max_concurrency=self._max_concurrency,
            tracer=_resolve(self._tracer),
            isolate_errors=self._isolate_errors,
            on_agent_error=self._on_agent_error,
            session_save_policy=self._session_save_policy,
            dispatcher=self._dispatcher,
            on_dispatch_error=self._on_dispatch_error,
        )

    async def stream(
        self,
        text: str,
        session_id: str = "",
        *,
        request: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Stream one turn: ``session`` → ``status``… → ``message``.

        Never raises for app-level failures: session open / runtime crash /
        reply hook all degrade to the fallback `message` and are logged via the
        `reactifact.chat` logger, so a web layer never delivers a 500 mid-stream.

        **Cancellation contract:** closing/`aclose()`-ing the returned generator
        (what a dropped SSE client does to the server's streaming task) cancels
        the in-flight turn — the `CancelledError` unwinds through `run_message`
        to `Runtime.astream`, whose `finally` cancels the runner task, so a slow
        agent or LLM call is stopped rather than left burning tokens. `stream`
        still saves the session in its own `finally` first.

        Turns on the same `session_id` are serialized (`_locked_session`): a
        second concurrent call for the same session waits for the first to
        finish instead of racing it to `session.save()` (§59 — no silent lost
        update). Different `session_id`s never block each other. This only
        covers calls made through this `ChatAssistant` instance — the
        lower-level `run_message` building block has no such guarantee, by
        design (see the module docstring).
        """
        async with self._locked_session(session_id):
            session: Session | None = None
            try:
                session = await self._open(session_id, request)
                runtime = self._build_runtime(session)
            except Exception:
                logger.exception(
                    "chat.ChatAssistant: failed to open session %r", session_id
                )
                # If the session did open but building the runtime failed, its
                # per-turn resources still need closing (a callable `resources=`
                # is never referenced again).
                if session is not None and callable(self._resources):
                    with suppress(Exception):
                        await session.context.resources.aclose()
                yield ChatEvent(
                    kind="message",
                    session_id=session_id,
                    payload=_fallback_payload(self._fallback_reply),
                )
                return
            yield ChatEvent(kind="session", session_id=session_id)
            try:
                async for event in run_message(
                    runtime,
                    text,
                    user_message=self._user_message,
                    reply=self._reply,
                    create_message=self._create_message,
                    status_kinds=self._status_kinds,
                    fallback_reply=self._fallback_reply,
                    session_id=session_id,
                    request=request,
                ):
                    yield event
            finally:
                try:
                    memory = self._memory
                    if memory is not None and memory.compact_commits is not None:
                        session.context.compact(keep_commits=memory.compact_commits)
                    await session.save()  # persist the conversation after the turn
                except Exception:
                    logger.exception(
                        "chat.ChatAssistant: failed to save session %r", session_id
                    )
                if callable(self._resources):
                    # A callable `resources=` builds a fresh RuntimeResources (and
                    # typically a fresh provider + HTTP client) on every turn —
                    # nothing else will ever reference this instance again, so
                    # it's this turn's job to close it. A shared instance passed
                    # directly is not touched here: it must outlive this turn.
                    try:
                        await session.context.resources.aclose()
                    except Exception:
                        logger.exception(
                            "chat.ChatAssistant: failed to close per-turn resources %r",
                            session_id,
                        )

    async def invoke(
        self,
        text: str,
        session_id: str = "",
        *,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one turn and return the terminal reply (aggregated stream)."""
        message: dict[str, Any] = {}
        async for event in self.stream(text, session_id=session_id, request=request):
            if event.kind == "message":
                message = dict(event.payload) or {"reply": self._fallback_reply}
        return message

    async def history(self, session_id: str = "") -> dict[str, Any]:
        """Reconstruct the chat thread of a persisted session.

        Read-only: a per-turn `resources=` factory is not invoked (no provider
        or user scope is built just to read artifacts). A shared
        `RuntimeResources` instance is attached as usual, so a custom
        `session_state` can still read it.
        """
        try:
            session = await self._open(session_id, build_resources=False)
        except Exception:
            logger.exception("chat.ChatAssistant: history open failed %r", session_id)
            return {"messages": []}
        if not session.loaded:
            return {"messages": []}
        if self._session_state is not None:
            return self._session_state(session.context)
        return default_session_state(session.context, user_message=self._user_message)

    def reply_fallback(self) -> dict[str, Any]:
        """The honest terminal payload when nothing was assembled (§59)."""
        return {"reply": self._fallback_reply, "waiting": False}


class Chat:
    """A typed, low-ceremony facade over `ChatAssistant`.

    `Chat(steps=[...], seed_type=Question, answer_type=Answer)` derives the
    user-message hook and the reply payload from the types; `seed_factory` and
    `per_turn` cover app-specific hooks (a request category, a secret vault).
    `traces=`/`trace_sample=`/`lifespan=` mirror `Pipeline`; `answer()` returns
    the typed `answer_type` instead of the transport payload.
    """

    def __init__(
        self,
        steps: Sequence[Step[Any]] | None = None,
        *,
        groups: Sequence[StepGroup] | None = None,
        store: SessionStore | str | Path | None = None,
        seed_type: type[BaseModel],
        seed_field: str = "text",
        seed_factory: Callable[[str, str, Mapping[str, Any]], BaseModel] | None = None,
        answer_type: type[BaseModel] | None = None,
        reply: Callable[[Context, str], dict[str, Any]] | ChatReply | None = None,
        session_state: Callable[[Context], dict[str, Any]] | None = None,
        resources: RuntimeResources | Callable[..., RuntimeResources] | None = None,
        llm: Any = None,
        budget: Budget | None = None,
        max_concurrency: int | None = None,
        tracer: Any = None,
        traces: bool | str | Path | Any | None = None,
        trace_sample: Any = None,
        lifespan: Callable[[], AbstractAsyncContextManager[RuntimeResources]]
        | None = None,
        memory: ChatMemory | None = None,
        session_save_policy: Literal["per_commit", "per_turn"] = "per_commit",
        status_kinds: Sequence[str] = ("status",),
        fallback_reply: str = "No reply assembled.",
        per_turn: Callable[
            [str, str, Mapping[str, Any]], AbstractAsyncContextManager[Any]
        ]
        | None = None,
        isolate_errors: bool = False,
        on_agent_error: Callable[[StepGroup, Event, BaseException], None] | None = None,
        create_message: Callable[..., str] | None = None,
        resume_pending: bool = False,
        dispatcher: Dispatcher | None = None,
        on_dispatch_error: DispatchErrorHandler | None = None,
    ):
        if steps is None and groups is None:
            raise TypeError("Chat requires steps= and/or groups=")
        if resources is None:
            resources = RuntimeResources(llm=llm)
        self.steps = list(steps) if steps is not None else None
        self.groups = list(groups) if groups is not None else None
        self.seed_type = seed_type
        self.seed_field = seed_field
        self.answer_type = answer_type
        self.per_turn = per_turn
        self._seed_factory = seed_factory
        self.resources = resources
        self.trace_sample = trace_sample
        self.trace_store: Any = None
        if traces:
            from .tracing import TraceStore

            self.trace_store = (
                TraceStore(str(traces))
                if isinstance(traces, (str, Path))
                else traces
                if not isinstance(traces, bool)
                else TraceStore(":memory:")
            )
            if tracer is None:
                from .tracing import Tracer

                tracer = Tracer(store=self.trace_store, sample=trace_sample)
        self.tracer = tracer
        self.lifespan = lifespan
        self._lifespan_cm: AbstractAsyncContextManager[RuntimeResources] | None = None
        self.resume_pending = resume_pending
        create = create_message or (
            self._resume_or_create if resume_pending else self._create_message
        )
        self.assistant = ChatAssistant(
            steps=steps,
            groups=groups,
            store=_as_store(store),
            user_message=seed_type,
            reply=reply or self._default_reply,
            session_state=session_state,
            resources=resources,
            budget=budget,
            max_concurrency=max_concurrency,
            tracer=tracer,
            create_message=create,
            status_kinds=status_kinds,
            fallback_reply=fallback_reply,
            isolate_errors=isolate_errors,
            on_agent_error=on_agent_error,
            session_save_policy=session_save_policy,
            memory=memory,
            dispatcher=dispatcher,
            on_dispatch_error=on_dispatch_error,
        )

    def _seed(
        self, text: str, session_id: str, request: Mapping[str, Any]
    ) -> BaseModel:
        if self._seed_factory is not None:
            return self._seed_factory(text, session_id, request)
        fields = getattr(self.seed_type, "model_fields", {})
        kwargs: dict[str, Any] = {self.seed_field: text}
        if "session_id" in fields:
            kwargs["session_id"] = session_id
        return self.seed_type(**kwargs)

    def _create_message(
        self,
        context: Context,
        text: str,
        session_id: str = "",
        request: Mapping[str, Any] | None = None,
    ) -> str:
        return context.create(self._seed(text, session_id, request or {})).id

    def _resume_or_create(
        self,
        context: Context,
        text: str,
        session_id: str = "",
        request: Mapping[str, Any] | None = None,
    ) -> str:
        """A pending question turns the next user message into its answer."""
        pending = context.latest_pending_question()
        if pending is not None:
            context.resume(pending.id, text)
            return pending.id
        return self._create_message(context, text, session_id, request)

    def _default_reply(self, context: Context, _msg_id: str) -> dict[str, Any]:
        pending = context.latest_pending_question()
        if pending is not None:
            question = pending.data
            return {
                "reply": question.question,
                "waiting": True,
                "question": {
                    "id": pending.id,
                    "kind": question.kind,
                    "question": question.question,
                    "schema": question.resolution_schema,
                    "notes": dict(question.notes),
                },
            }
        artifact = None
        if self.answer_type is not None:
            artifact = context.latest(self.answer_type)
        if artifact is None:
            for candidate in reversed(context.list_artifacts()):
                if not isinstance(candidate.data, self.seed_type):
                    artifact = candidate
                    break
        if artifact is None:
            return {"reply": self.assistant._fallback_reply, "waiting": False}
        data = artifact.data
        payload: dict[str, Any] = {
            "reply": str(getattr(data, "text", "")),
            "waiting": False,
        }
        sources = getattr(data, "sources", None)
        if sources is not None:
            payload["sources"] = list(sources)
        payload["answer"] = data.model_dump(mode="json")
        return payload

    async def __aenter__(self) -> Chat:
        if self.lifespan is not None:
            self._lifespan_cm = self.lifespan()
            self.resources = await self._lifespan_cm.__aenter__()
            self.assistant._resources = self.resources
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._lifespan_cm is not None:
            await self._lifespan_cm.__aexit__(exc_type, exc, tb)
            self._lifespan_cm = None

    def _check_lifespan(self) -> None:
        if self.lifespan is not None and self._lifespan_cm is None:
            raise RuntimeError(
                "this Chat has a lifespan; use `async with chat:` and "
                "`await chat.invoke(...)`"
            )

    def close(self) -> None:
        """Close the trace store opened by `traces=` (no-op otherwise)."""
        if self.trace_store is not None and hasattr(self.trace_store, "close"):
            self.trace_store.close()

    async def stream(
        self,
        text: str,
        session_id: str = "",
        *,
        request: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        self._check_lifespan()
        if self.per_turn is None:
            async for event in self.assistant.stream(
                text, session_id=session_id, request=request
            ):
                yield event
            return
        async with self.per_turn(text, session_id, request or {}):
            async for event in self.assistant.stream(
                text, session_id=session_id, request=request
            ):
                yield event

    async def invoke(
        self,
        text: str,
        session_id: str = "",
        *,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        async for event in self.stream(text, session_id=session_id, request=request):
            if event.kind == "message":
                payload = dict(event.payload)
        return payload

    async def answer(
        self,
        text: str,
        session_id: str = "",
        *,
        request: Mapping[str, Any] | None = None,
    ) -> BaseModel:
        """Run one turn and return the `answer_type` artifact's model.

        `invoke()` stays the transport-shaped dict; `answer()` is the typed
        version for application code. It requires `answer_type=...` on the
        `Chat`.
        """
        if self.answer_type is None:
            raise TypeError("Chat.answer requires answer_type=...")
        payload = await self.invoke(text, session_id=session_id, request=request)
        raw = payload.get("answer")
        if isinstance(raw, Mapping):
            return self.answer_type.model_validate(raw)
        session = await self.assistant._open(session_id)
        artifact = session.context.latest(self.answer_type)
        if artifact is None:
            raise LookupError(
                f"Chat.answer: no {self.answer_type.__name__} in session {session_id!r}"
            )
        return artifact.data

    async def history(self, session_id: str = "") -> dict[str, Any]:
        self._check_lifespan()
        return await self.assistant.history(session_id=session_id)

    def validate(
        self,
        *,
        seeds: Sequence[type | str] = (),
        terminal: Sequence[type | str] = (),
    ) -> ValidationReport:
        """Static contract check for this chat's steps (no run, no LLM)."""
        from .validate import validate_contracts

        inferred_seeds: list[type | str] = list(seeds)
        inferred_terminal: list[type | str] = list(terminal)
        if not inferred_seeds and self.seed_type is not None:
            inferred_seeds.append(self.seed_type)
        if not inferred_terminal and self.answer_type is not None:
            inferred_terminal.append(self.answer_type)
        return validate_contracts(
            steps=self.steps,
            groups=self.groups,
            seeds=inferred_seeds,
            terminal=inferred_terminal,
        )

    def describe(self) -> ContractReport:
        """The static contract plus seed/answer JSON Schemas."""
        from .contracts.report import ContractReport, describe_contracts

        groups = normalize_units(steps=self.steps, groups=self.groups)
        base = describe_contracts(groups)
        return ContractReport(
            groups=base.groups,
            seed_schema=(
                self.seed_type.model_json_schema()
                if self.seed_type is not None
                else None
            ),
            answer_schema=(
                self.answer_type.model_json_schema()
                if self.answer_type is not None
                else None
            ),
        )

    def router(self, **kwargs: Any) -> Any:
        """A FastAPI `APIRouter` for this chat (SSE, history, delete, health).

        Equivalent to `create_chat_router(chat, **kwargs)`; FastAPI is imported
        lazily, so the core package does not depend on it. Use `routes=` to
        expose `/chat`, `/chat/stream`, `/chat/history`, `/chat/clear`.
        """
        from .web import create_chat_router

        return create_chat_router(self, **kwargs)

    def http(self, **kwargs: Any) -> Any:
        """A ready FastAPI app for this chat (sessions, history, traces)."""
        from .web import create_chat_app

        return create_chat_app(self, **kwargs)

    def test_client(self, *, traces: bool = True) -> Any:
        """A copy with an isolated in-memory session store and traces."""
        from .testing.client import TestChat

        return TestChat(self, traces=traces)

    @property
    def store(self) -> SessionStore:
        """The underlying session store (used by `create_chat_router`)."""
        return self.assistant.store

    def reply_fallback(self) -> dict[str, Any]:
        return self.assistant.reply_fallback()


def _as_store(store: SessionStore | str | Path | None) -> SessionStore:
    from .checkpoints import FileKVBackend, InMemoryKVBackend

    if store is None:
        return SessionStore(InMemoryKVBackend())
    if isinstance(store, SessionStore):
        return store
    return SessionStore(FileKVBackend(str(store)))


__all__ = [
    "Chat",
    "ChatAssistant",
    "ChatReply",
    "ChatEvent",
    "ChatEventKind",
    "ChatMemory",
    "default_session_state",
    "run_message",
]
