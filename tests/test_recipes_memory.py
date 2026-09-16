"""`reactifact.recipes.memory` — bounded conversation memory (§27, §37).

`WindowSummarizer` + `WindowPruner` replace the hand-rolled Summarize/Prune
pair that used to live only in `examples/summarize/main.py` — same behavior,
now parametrized instead of copy-pasted per app.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Artifact, Consume, Context, Runtime, create_agent
from reactifact.recipes import RollingDigestSummarizer, WindowPruner, WindowSummarizer


class Msg(BaseModel):
    role: str
    text: str


class Question(BaseModel):
    text: str


class FinalResponse(BaseModel):
    text: str


class Summary(BaseModel):
    round: int
    text: str


class Digest(BaseModel):
    text: str


def _build(round_no: int, text: str) -> Summary:
    return Summary(round=round_no, text=text)


def _build_digest(text: str) -> Digest:
    return Digest(text=text)


async def _offline_summarize(context: Context, history: str) -> str | None:
    return None  # forces the fallback path, deterministic and offline


async def _offline_digest(
    context: Context, previous: str, stale: list[Artifact[Any]]
) -> str | None:
    return None  # forces the fallback path, deterministic and offline


async def _own_render_digest(
    context: Context, previous: str, stale: list[Artifact[Any]]
) -> str | None:
    # Stands in for a caller with its own role/content prompt builder — the
    # recipe hands over raw artifacts so this can inspect `.data` per type
    # instead of round-tripping through a pre-rendered string.
    parts = [f"{type(a.data).__name__}:{a.data.text}" for a in stale]
    return f"{previous};{','.join(parts)}" if previous else ",".join(parts)


def _flow(*, window: int = 4, every: int = 2, keep: int = 4) -> Agent:
    return create_agent(
        "memory",
        consumes=[Consume(Msg)],
        produces=[
            WindowSummarizer(
                Msg,
                Summary,
                summarize=_offline_summarize,
                build=_build,
                window=window,
                every=every,
            ),
            WindowPruner(Msg, keep=keep),
        ],
    )


def _run(messages: list[str]) -> Context:
    async def _arun() -> Context:
        ctx = Context()
        runtime = Runtime(ctx, agents=[_flow()])
        for i, text in enumerate(messages):
            ctx.create(Msg(role="user" if i % 2 == 0 else "assistant", text=text))
            await runtime.arun()
        return ctx

    return asyncio.run(_arun())


def test_summarizes_every_n_messages_with_stable_round_ids():
    ctx = _run([f"msg {i}" for i in range(4)])
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.round)
    assert [s.data.round for s in summaries] == [1, 2]
    assert ctx.get("summary:1") is not None
    assert ctx.get("summary:2") is not None


def test_offline_summarizer_produces_honest_fallback_text():
    ctx = _run(["hello", "world"])
    summary = ctx.get("summary:1")
    assert summary is not None
    assert summary.data.text.startswith("(offline memory)")


def test_pruner_keeps_only_the_window():
    ctx = _run([f"msg {i}" for i in range(10)])
    assert len(ctx.list_artifacts(Msg)) == 4  # keep=4 in _flow()


def test_pruner_alone_bounds_messages_without_a_summarizer():
    async def _arun() -> Context:
        ctx = Context()
        agent = create_agent(
            "prune-only", consumes=[Consume(Msg)], produces=[WindowPruner(Msg, keep=2)]
        )
        runtime = Runtime(ctx, agents=[agent])
        for i in range(5):
            ctx.create(Msg(role="user", text=f"m{i}"))
            await runtime.arun()
        return ctx

    ctx = asyncio.run(_arun())
    assert len(ctx.list_artifacts(Msg)) == 2
    remaining = sorted(ctx.list_artifacts(Msg), key=lambda m: m.data.text)
    assert [m.data.text for m in remaining] == ["m3", "m4"]


def test_no_summary_before_the_first_round_completes():
    ctx = _run(["only one message"])
    assert ctx.list_artifacts(Summary) == []


def _digest_flow(*, window: int = 4, trigger: int = 6) -> Agent:
    return create_agent(
        "digest",
        consumes=[Consume(Msg)],
        produces=[
            RollingDigestSummarizer(
                Msg,
                Digest,
                summarize=_offline_digest,
                build=_build_digest,
                window=window,
                trigger=trigger,
            ),
        ],
    )


def _run_digest(messages: list[str], *, window: int = 4, trigger: int = 6) -> Context:
    async def _arun() -> Context:
        ctx = Context()
        runtime = Runtime(ctx, agents=[_digest_flow(window=window, trigger=trigger)])
        for i, text in enumerate(messages):
            ctx.create(Msg(role="user" if i % 2 == 0 else "assistant", text=text))
            await runtime.arun()
        return ctx

    return asyncio.run(_arun())


def test_digest_keeps_only_the_window_raw():
    ctx = _run_digest([f"msg {i}" for i in range(10)], window=4, trigger=6)
    assert len(ctx.list_artifacts(Msg)) == 4
    remaining = sorted(ctx.list_artifacts(Msg), key=lambda m: m.data.text)
    assert [m.data.text for m in remaining] == ["msg 6", "msg 7", "msg 8", "msg 9"]


def test_digest_folds_stale_messages_and_grows():
    ctx = _run_digest([f"msg {i}" for i in range(10)], window=4, trigger=6)
    digest = ctx.get("digest")
    assert digest is not None
    assert "msg 0" in digest.data.text
    assert "msg 3" in digest.data.text  # folded in the second round too


def test_digest_offline_fallback_carries_previous_text_forward():
    ctx = _run_digest([f"msg {i}" for i in range(10)], window=4, trigger=6)
    digest = ctx.get("digest")
    assert digest is not None
    assert digest.data.text.count("(offline memory)") == 2  # two folds happened


def test_no_digest_before_trigger_is_reached():
    ctx = _run_digest([f"msg {i}" for i in range(5)], window=4, trigger=6)
    assert ctx.get("digest") is None
    assert len(ctx.list_artifacts(Msg)) == 5


def test_digest_merges_multiple_message_types_and_calls_own_render():
    async def _arun() -> Context:
        ctx = Context()
        agent = create_agent(
            "digest-multi",
            consumes=[Consume(Question), Consume(FinalResponse)],
            produces=[
                RollingDigestSummarizer(
                    [Question, FinalResponse],
                    Digest,
                    summarize=_own_render_digest,
                    build=_build_digest,
                    window=4,
                    trigger=6,
                ),
            ],
        )
        runtime = Runtime(ctx, agents=[agent])
        for i in range(10):
            if i % 2 == 0:
                ctx.create(Question(text=f"q{i}"))
            else:
                ctx.create(FinalResponse(text=f"r{i}"))
            await runtime.arun()
        return ctx

    ctx = asyncio.run(_arun())
    remaining_questions = ctx.list_artifacts(Question)
    remaining_responses = ctx.list_artifacts(FinalResponse)
    assert len(remaining_questions) + len(remaining_responses) == 4
    digest = ctx.get("digest")
    assert digest is not None
    assert "Question:q0" in digest.data.text
    assert "FinalResponse:r1" in digest.data.text
