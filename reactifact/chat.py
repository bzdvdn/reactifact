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
    Callable,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel

from .agents import Agent
from .budget import Budget
from .context import Context
from .events import Event
from .resources import RuntimeResources
from .runtime import Runtime
from .session import Session, SessionStore

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


async def run_message(
    runtime: Runtime,
    text: str,
    *,
    user_message: type[BaseModel],
    reply: Callable[[Context, str], dict[str, Any]],
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
    artifact) — detected from the callable's own arity, so the 2-arg shape
    keeps working unchanged.

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
        if create_message is not None:
            msg_id = _call_create_message(create_message, ctx, text, session_id)
        else:
            msg_id = ctx.create(user_message(text=text, session_id=session_id)).id
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
        payload = reply(ctx, msg_id)
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
    for artifact in sorted(ctx.list_artifacts(), key=lambda a: a.created_at):
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


def _resolve_with_session(value: Any, session_id: str) -> Any:
    """Like `_resolve`, but passes `session_id` to `value` when it declares a
    parameter for it — the per-request factory shape
    (`resources=lambda session_id: build_resources(session_id)`) alongside
    the pre-existing zero-arg one (`resources=lambda: build_resources()`),
    which keeps working unchanged. `ChatAssistant` already knows `session_id`
    at the point it resolves `resources=`; this just lets a factory opt into
    reading it instead of reaching for a contextvar/side-channel to get
    per-request data (e.g. an authenticated user) into `RuntimeResources`.
    """
    if not callable(value):
        return value
    return value(session_id) if _accepts_arg(value, 1) else value()


def _call_create_message(
    create_message: Callable[..., str], ctx: Context, text: str, session_id: str
) -> str:
    """Calls `create_message` with `session_id` appended when it declares a
    third parameter for it (`(ctx, text, session_id) -> msg_id`), alongside
    the pre-existing two-arg shape (`(ctx, text) -> msg_id`)."""
    if _accepts_arg(create_message, 3):
        return create_message(ctx, text, session_id)
    return create_message(ctx, text)


# --- the canonical chat assistant ----------------------------------------- #


class ChatAssistant:
    """Session-persisted chat over the runtime, for the canonical contract.

    Configure with hooks — the assistant owns sessions, the turn loop and
    history. `agents`/`resources` accept values or callables (resolved per
    request, so a fresh `RuntimeResources` can replace providers).

    A callable `resources=` is assumed to build a fresh, turn-scoped
    `RuntimeResources` each call (e.g. `resources=lambda: build_resources()`)
    — `stream()` closes it (`RuntimeResources.aclose()`) after every turn, so
    its provider's HTTP client doesn't leak. Pass a plain `RuntimeResources`
    instance instead when you want one shared, long-lived provider across
    turns/sessions — that instance is never closed automatically; close it
    yourself at real shutdown.

    `session_save_policy=` passes straight through to `Runtime` — `"per_turn"`
    trades finer crash-resilience granularity (a save at every generation
    boundary, the default) for one `session.save()` per turn, worthwhile once a
    multi-stage pipeline routinely produces several generations per turn (see
    `Runtime.__init__`'s own docstring for the trade-off).

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
            agents=ALL_AGENTS,
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
        agents: Sequence[Agent] | Callable[[], Sequence[Agent]],
        user_message: type[BaseModel],
        reply: Callable[[Context, str], dict[str, Any]],
        session_state: Callable[[Context], dict[str, Any]] | None = None,
        resources: RuntimeResources | Callable[..., RuntimeResources] | None = None,
        budget: Budget | None = None,
        max_concurrency: int | None = None,
        tracer: Any = None,
        create_message: Callable[..., str] | None = None,
        status_kinds: Sequence[str] = ("status",),
        fallback_reply: str = "No reply assembled.",
        isolate_errors: bool = False,
        on_agent_error: Callable[[Agent, Event, BaseException], None] | None = None,
        session_save_policy: Literal["per_commit", "per_turn"] = "per_commit",
    ):
        self.store = store
        self._agents = agents
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

    async def _open(self, session_id: str) -> Session:
        return await self.store.open(
            session_id, resources=_resolve_with_session(self._resources, session_id)
        )

    def _build_runtime(self, session: Session) -> Runtime:
        return Runtime(
            session.context,
            agents=list(_resolve(self._agents)),
            session=session,
            budget=self._budget,
            max_concurrency=self._max_concurrency,
            tracer=_resolve(self._tracer),
            isolate_errors=self._isolate_errors,
            on_agent_error=self._on_agent_error,
            session_save_policy=self._session_save_policy,
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
            try:
                session = await self._open(session_id)
                runtime = self._build_runtime(session)
            except Exception:
                logger.exception(
                    "chat.ChatAssistant: failed to open session %r", session_id
                )
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
        """Reconstruct the chat thread of a persisted session."""
        try:
            session = await self._open(session_id)
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


__all__ = [
    "ChatAssistant",
    "ChatEvent",
    "ChatEventKind",
    "default_session_state",
    "run_message",
]
