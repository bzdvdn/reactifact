"""recipes — bounded conversation memory: two memory models, pick one.

Long-running chat memory is just state (§27, §37): message artifacts
accumulate, and a summarizer condenses the ones falling out of the window.
Two shapes cover the common cases, and they are *not* interchangeable:

- `WindowSummarizer` + `WindowPruner` — periodic checkpoints. Every `every`
  messages, snapshot the current window into a fresh summary artifact (one
  per round, all of them kept); `WindowPruner` separately bounds the raw
  window by deleting what falls outside it.
- `RollingDigestSummarizer` — one growing digest. Once the conversation
  passes `trigger` messages, fold everything older than `window` into a
  single digest artifact that keeps accumulating (each fold rewrites it from
  the previous digest text plus the newly stale messages), deleting the
  folded messages itself. `WindowPruner` alone can't do this — it deletes
  without folding first, so using it in place of this recipe loses content
  instead of condensing it.

All are plain `Produce`s — drop them into an `Agent.produces` list next to
whatever else reacts to the message type.

Domain owns *how* to summarize (the `summarize` callback) and *what* the
summary/digest artifact looks like (`build`); the recipe only owns window
size, cadence/trigger, and idempotency bookkeeping — the same split as
`materialize_doc` owning provenance while the caller owns the document
factory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..produce import Produce, ProduceCall
from ..structured import OnStructuredError, llm_reply

MsgT = TypeVar("MsgT", bound=BaseModel)
SummaryT = TypeVar("SummaryT", bound=BaseModel)


def _default_render(messages: list[Artifact[Any]]) -> str:
    lines = []
    for a in messages:
        role = getattr(a.data, "role", None)
        text = getattr(a.data, "text", None)
        lines.append(
            f"{role}: {text}" if role is not None and text is not None else str(a.data)
        )
    return "\n".join(lines)


def _default_fallback(history: str) -> str:
    return f"(offline memory) {history[:140]}"


def _default_digest_fallback(previous: str, stale: list[Artifact[Any]]) -> str:
    return f"{previous}\n(offline memory) {_default_render(stale)[:140]}".strip()


def _default_digest_text(artifact: Artifact[Any]) -> str:
    return str(getattr(artifact.data, "text", artifact.data))


class WindowSummarizer(Produce[SummaryT], Generic[MsgT, SummaryT]):
    """Condenses the recent window of `message_type` artifacts into a summary
    artifact every `every` messages (§27).

    Idempotent by construction: the summary id is derived from the message
    count, so re-running the same generation never produces a duplicate.
    """

    def __init__(
        self,
        message_type: type[MsgT],
        artifact_type: type[SummaryT],
        *,
        summarize: Callable[[Context, str], Awaitable[str | None]],
        build: Callable[[int, str], SummaryT],
        window: int = 8,
        every: int = 4,
        render: Callable[[list[Artifact[MsgT]]], str] = _default_render,
        fallback: Callable[[str], str] = _default_fallback,
        order_key: Callable[[Artifact[MsgT]], Any] = lambda a: a.created_at,
        id_of: Callable[[int], str] = lambda round_no: f"summary:{round_no}",
    ):
        super().__init__(artifact_type=artifact_type)
        self.message_type = message_type
        self.summarize = summarize
        self.build = build
        self.window = window
        self.every = every
        self.render = render
        self.fallback = fallback
        self.order_key = order_key
        self.id_of = id_of

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        messages = context.list_artifacts(self.message_type)
        count = len(messages)
        if count == 0 or count % self.every != 0:
            return None
        round_no = count // self.every
        summary_id = self.id_of(round_no)
        if context.get(summary_id) is not None:
            return None  # this round's summary already exists (§42)
        recent = sorted(messages, key=self.order_key)[-self.window :]
        history = self.render(recent)
        text = await self.summarize(context, history)
        if text is None:
            text = self.fallback(history)
        self.effects.create(self.build(round_no, text), id=summary_id)
        return None


class WindowPruner(Produce[MsgT], Generic[MsgT]):
    """Deletes `message_type` artifacts older than `keep` (ordered by
    `order_key`, default `created_at`). Standalone-useful — pair it with
    `WindowSummarizer` for full sliding-window memory, or use it alone to
    just bound how many messages a context keeps.
    """

    def __init__(
        self,
        message_type: type[MsgT],
        *,
        keep: int = 8,
        order_key: Callable[[Artifact[MsgT]], Any] = lambda a: a.created_at,
    ):
        super().__init__(artifact_type=message_type)
        self.message_type = message_type
        self.keep = keep
        self.order_key = order_key

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        messages = sorted(context.list_artifacts(self.message_type), key=self.order_key)
        # `max(0, ...)`: a plain negative slice bound wraps from the end in
        # Python (`messages[:-1]` means "all but the last"), which would prune
        # the wrong messages while the window hasn't filled up yet.
        old = messages[: max(0, len(messages) - self.keep)]
        if not old:
            return None
        for message in old:
            self.effects.delete(message.id)
        return None


class RollingDigestSummarizer(Produce[SummaryT], Generic[MsgT, SummaryT]):
    """Folds everything older than `window` into one growing digest, once the
    conversation exceeds `trigger` messages (§27, §37).

    Different memory model from `WindowSummarizer`: that one snapshots the
    *current* window into a fresh artifact every `every` messages, keeping
    every past window around. This one keeps a single digest artifact that
    accumulates — each fold rewrites it from the previous digest text plus
    whatever just fell out of the window — and deletes the folded messages,
    so the window it manages stays raw and bounded without a separate
    `WindowPruner` (folding into the digest and deleting the source messages
    have to happen together, or the content is lost instead of condensed).

    `message_type` accepts a single type or a sequence of types (e.g. a
    `Question`/`FinalResponse` pair that aren't one model), merged and
    ordered by `order_key` before the window/trigger math runs.

    `summarize`/`fallback` receive the stale artifacts as a raw list, not a
    pre-rendered string — the recipe never flattens messages into text on
    your behalf. That's deliberate: a caller with an existing role/content
    prompt builder (or a two-type interleave that a `role`+`text` string
    can't represent) writes its own rendering inside `summarize` instead of
    round-tripping through this recipe's string format. `llm_digest_summarizer`
    below is the opt-in default renderer for callers who *do* want a plain
    text-in/text-out prompt.
    """

    def __init__(
        self,
        message_type: type[MsgT] | Sequence[type[MsgT]],
        artifact_type: type[SummaryT],
        *,
        summarize: Callable[[Context, str, list[Artifact[MsgT]]], Awaitable[str | None]],
        build: Callable[[str], SummaryT],
        window: int = 8,
        trigger: int = 12,
        fallback: Callable[[str, list[Artifact[MsgT]]], str] = _default_digest_fallback,
        digest_text: Callable[[Artifact[SummaryT]], str] = _default_digest_text,
        order_key: Callable[[Artifact[MsgT]], Any] = lambda a: a.created_at,
        digest_id: str = "digest",
    ):
        super().__init__(artifact_type=artifact_type)
        self.message_types: tuple[type[MsgT], ...] = (
            (message_type,) if isinstance(message_type, type) else tuple(message_type)
        )
        self.summarize = summarize
        self.build = build
        self.window = window
        self.trigger = trigger
        self.fallback = fallback
        self.digest_text = digest_text
        self.order_key = order_key
        self.digest_id = digest_id

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        messages = sorted(
            (a for t in self.message_types for a in context.list_artifacts(t)),
            key=self.order_key,
        )
        if len(messages) <= self.trigger:
            return None
        stale = messages[: len(messages) - self.window]
        if not stale:
            return None
        previous = context.get(self.digest_id)
        previous_text = self.digest_text(previous) if previous is not None else ""
        text = await self.summarize(context, previous_text, stale)
        if text is None:
            text = self.fallback(previous_text, stale)
        # `create(..., id=...)` doubles as refresh when the id already exists
        # (§42/§43) — no separate update-vs-create branch needed.
        self.effects.create(self.build(text), id=self.digest_id)
        for message in stale:
            self.effects.delete(message.id)
        return None


def llm_summarizer(
    system: str,
    *,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_error: OnStructuredError | None = None,
) -> Callable[[Context, str], Awaitable[str | None]]:
    """Builds a `WindowSummarizer(summarize=...)` callback from a system
    prompt, via `llm_reply` — the common case, no custom schema class needed.
    """

    async def _summarize(context: Context, history: str) -> str | None:
        return await llm_reply(
            context,
            system=system,
            user=history,
            attempts=attempts,
            temperature=temperature,
            max_tokens=max_tokens,
            on_error=on_error,
        )

    return _summarize


def llm_digest_summarizer(
    system: str,
    *,
    render: Callable[[list[Artifact[Any]]], str] = _default_render,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_error: OnStructuredError | None = None,
) -> Callable[[Context, str, list[Artifact[Any]]], Awaitable[str | None]]:
    """Builds a `RollingDigestSummarizer(summarize=...)` callback from a
    system prompt, via `llm_reply` — the common case, no custom callback
    needed. Renders the stale artifacts with `render` (default: `role: text`
    lines) and puts the previous digest and that rendering into two labeled
    sections of one user turn.

    Reach for your own `summarize` callback instead when messages don't
    reduce to a `role`/`text` string (e.g. structured role/content prompt
    turns, or artifacts of more than one type) — this helper is the opt-in
    default, not the only path.
    """

    async def _summarize(
        context: Context, previous: str, stale: list[Artifact[Any]]
    ) -> str | None:
        history = render(stale)
        user = (
            f"Previous memory:\n{previous}\n\nNew messages to fold in:\n{history}"
            if previous
            else history
        )
        return await llm_reply(
            context,
            system=system,
            user=user,
            attempts=attempts,
            temperature=temperature,
            max_tokens=max_tokens,
            on_error=on_error,
        )

    return _summarize


__all__ = [
    "RollingDigestSummarizer",
    "WindowSummarizer",
    "WindowPruner",
    "llm_digest_summarizer",
    "llm_summarizer",
]
