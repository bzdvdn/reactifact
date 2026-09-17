"""recipes.identity — per-request data into a turn via a contextvar (§24, §42).

`ChatAssistant` builds one long-lived instance per process; neither its
`resources=` factory (opt-in `session_id`-arity only, see `chat.py`'s own
docstring) nor an agent's `produce()` receive arbitrary per-request HTTP
data — only the caller of `.stream()`/`.invoke()` (a web route) knows which
request is in flight, and that data is often richer than a bare `session_id`
(an authenticated user, a tenant id, a feature-flag set, ...). A
`contextvars.ContextVar` set right before that call is visible from there
through `Runtime.arun()`: same asyncio task, no thread/task hop in between.

`SeedIdentity` is the other half: a plain reactive `Produce` that reads that
contextvar once per turn and writes it as an ordinary, create-or-refresh
artifact into the *same* `Context` the turn's other artifacts live in —
deliberately not a `resources`-side dict keyed by `session_id`:
`Context.to_kv`/`SessionStore` checkpoint artifacts, never `resources`, so a
value that belongs with the session should be an artifact (stays scoped to
its own session, survives a restart the same way the rest of the turn does)
rather than an unbounded process-lifetime dict.

    identity_var: ContextVar[RequestIdentity | None] = ContextVar("request", default=None)

    class UserAgent(Agent):
        consumes = [Consume(Question)]
        produces = [
            SeedIdentity(UserContext, identity_var, identity_id="user_context",
                         extract=lambda ri: ri.user, fallback=lambda: UserContext(uid="unknown")),
        ]

    # right before assistant.stream()/.invoke():
    token = identity_var.set(RequestIdentity(session_id=sid, user=user))
    try:
        async for event in assistant.stream(text, session_id=sid): ...
    finally:
        identity_var.reset(token)
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..events import Event
from ..produce import Produce

IdentityT = TypeVar("IdentityT", bound=BaseModel)


class SeedIdentity(Produce[IdentityT], Generic[IdentityT]):
    """Writes `identity_var`'s current value as an `identity_type` artifact
    (id `identity_id`) once per turn.

    `extract` pulls the artifact data out of whatever the contextvar holds
    when it isn't already that exact shape (e.g. a richer per-request
    envelope carrying more than one field) — omit it when the contextvar
    already holds `identity_type` directly. `fallback` supplies a value when
    the contextvar is unset (e.g. a request made outside the web layer, a
    test) instead of silently skipping the write — omit it to skip instead
    (no artifact written that turn).
    """

    def __init__(
        self,
        identity_type: type[IdentityT],
        identity_var: ContextVar[Any],
        *,
        identity_id: str = "identity",
        extract: Callable[[Any], IdentityT | None] | None = None,
        fallback: Callable[[], IdentityT] | None = None,
    ) -> None:
        super().__init__(artifact_type=identity_type)
        self._identity_var = identity_var
        self._identity_id = identity_id
        self._extract = extract
        self._fallback = fallback

    async def produce(
        self,
        context: Context,
        inputs: list[Artifact[Any]],
        event: Event | None = None,
    ) -> None:
        raw = self._identity_var.get()
        identity: IdentityT | None
        if raw is not None and self._extract is not None:
            identity = self._extract(raw)
        else:
            identity = raw
        if identity is None:
            if self._fallback is None:
                return None
            identity = self._fallback()
        self.effects.upsert(identity, id=self._identity_id)
        return None


__all__ = ["SeedIdentity"]
