"""reactifact.request — the per-run request context (§24, §52).

`Runtime.arun(..., request=...)` (and `ChatAssistant.stream/invoke`) attach a
read-only mapping for the duration of one turn; steps read it as
`call.request`. This is the first-class version of the per-request data
(an authenticated user, a tenant id, a correlation id from an HTTP header)
that apps otherwise thread through a hand-rolled `ContextVar` or stuff into
`resources` — which is wrong when the same `RuntimeResources` is shared across
concurrent requests.

It is implemented with a `ContextVar` *internally*, so it is propagated to the
child tasks a generation fans out to, and isolated between concurrent turns
(each `asyncio.Task` gets its own context). Callers never touch the var: pass
`request=` at the entry point, read `call.request` in a step.

    runtime = Runtime(ctx, agents=[...])
    await runtime.arun(request={"user": "bob", "tenant": "acme"})

    @step(produces=(Answer,))
    async def answer(call: StepCall) -> Answer:
        user = call.request.get("user", "anonymous")
        return Answer(text=f"hello, {user}")

`request=None` (the default) means "no explicit request": a nested run (e.g.
`AgentAsTool`) inherits whatever is active, rather than clearing it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from types import MappingProxyType
from typing import Any

_EMPTY: Mapping[str, Any] = MappingProxyType({})


class CurrentRequest(Mapping[str, Any]):
    """Read-only per-turn request data, injected as a typed collaborator.

    A thin `Mapping` wrapper (not a pydantic model, so it never looks like an
    artifact) around `Runtime.arun(request=...)`. Use it instead of
    `call.request` / `current_request()` when a signature should show the
    dependency:

        @step(consumes=Question, produces=Answer)
        async def answer(question: Question, request: CurrentRequest) -> Answer:
            tenant = request.get("tenant")
            ...

    A parameter named `request` is injected even when unannotated; a real
    artifact type called `CurrentRequest` can still be read explicitly as
    `Artifact[CurrentRequest]`.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"CurrentRequest({dict(self._data)!r})"


_REQUEST: ContextVar[Mapping[str, Any]] = ContextVar(
    "reactifact_request", default=_EMPTY
)


def current_request() -> Mapping[str, Any]:
    """The active turn's request mapping (empty when none was set)."""
    return _REQUEST.get()


def set_request(request: Mapping[str, Any] | None) -> Token[Mapping[str, Any]]:
    """Installs `request` for the current task; returns the reset token."""
    return _REQUEST.set(request or _EMPTY)


def reset_request(token: Token[Mapping[str, Any]]) -> None:
    """Restores the previous request (call in a `finally`)."""
    _REQUEST.reset(token)


__all__ = ["CurrentRequest", "current_request", "reset_request", "set_request"]
