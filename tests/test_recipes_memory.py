"""`reactifact.recipes.memory` — bounded conversation memory (§27, §37).

`WindowSummarizer` replaces the hand-rolled Summarize step that used to live
only in `examples/summarize/main.py`; `WindowPruner` is the standalone pruning
tool, and `RollingDigestSummarizer` is the folding + pruning recipe.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel
from reactifact import Agent, Artifact, Consume, Context, Runtime
from reactifact.contracts import describe_contracts
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


def _flow(*, window: int = 4, every: int = 2) -> Agent:
    return Agent(
        "memory",
        consumes=[Consume(Msg)],
        steps=[
            WindowSummarizer(
                Msg,
                Summary,
                summarize=_offline_summarize,
                build=_build,
                window=window,
                every=every,
            ),
        ],
    )


def _prune_flow(*, keep: int = 4) -> Agent:
    return Agent(
        "prune",
        consumes=[Consume(Msg)],
        steps=[WindowPruner(Msg, keep=keep)],
    )


def _run(messages: list[str], *, every: int = 2) -> Context:
    async def _arun() -> Context:
        ctx = Context()
        runtime = Runtime(ctx, groups=[_flow(every=every)])
        for i, text in enumerate(messages):
            ctx.create(Msg(role="user" if i % 2 == 0 else "assistant", text=text))
            await runtime.arun()
        return ctx

    return asyncio.run(_arun())


def _run_prune(messages: list[str], *, keep: int = 4) -> Context:
    async def _arun() -> Context:
        ctx = Context()
        runtime = Runtime(ctx, groups=[_prune_flow(keep=keep)])
        for text in messages:
            ctx.create(Msg(role="user", text=text))
            await runtime.arun()
        return ctx

    return asyncio.run(_arun())


def test_summarizes_every_n_messages_with_stable_round_ids():
    ctx = _run([f"msg {i}" for i in range(4)])
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.round)
    assert [s.data.round for s in summaries] == [1, 2]
    assert ctx.get("summary:1") is not None
    assert ctx.get("summary:2") is not None


def test_summarizer_keeps_producing_rounds_without_a_pruner():
    ctx = _run([f"msg {i}" for i in range(20)], every=4)
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.round)
    assert [s.data.round for s in summaries] == [1, 2, 3, 4, 5]


def test_summarizer_handles_a_batch_of_new_messages():
    async def _arun() -> Context:
        ctx = Context()
        runtime = Runtime(ctx, groups=[_flow(every=4)])
        for i in range(5):
            ctx.create(Msg(role="user", text=f"m{i}"))
        await runtime.arun()
        for i in range(5, 9):
            ctx.create(Msg(role="user", text=f"m{i}"))
        await runtime.arun()
        return ctx

    ctx = asyncio.run(_arun())
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.round)
    assert [s.data.round for s in summaries] == [1, 2]


def test_offline_summarizer_produces_honest_fallback_text():
    ctx = _run(["hello", "world"])
    summary = ctx.get("summary:1")
    assert summary is not None
    assert summary.data.text.startswith("(offline memory)")


def test_pruner_keeps_only_the_window():
    ctx = _run_prune([f"msg {i}" for i in range(10)])
    assert len(ctx.list_artifacts(Msg)) == 4  # keep=4


def test_pruner_alone_bounds_messages_without_a_summarizer():
    async def _arun() -> Context:
        ctx = Context()
        agent = Agent(
            "prune-only", consumes=[Consume(Msg)], steps=[WindowPruner(Msg, keep=2)]
        )
        runtime = Runtime(ctx, groups=[agent])
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


def test_memory_contracts_show_deletes_not_false_producers():
    report = describe_contracts([_flow(), _prune_flow(), _digest_flow()])
    by_name = {group.name: group for group in report.groups}

    assert "Msg" not in by_name["memory"].produces
    assert "Msg" not in by_name["prune"].produces
    assert by_name["prune"].deletes == ("Msg",)

    digest = by_name["digest"]
    assert digest.produces == ("Digest",)
    assert digest.deletes == ("Msg",)


def test_memory_configuration_is_validated():
    with pytest.raises(ValueError, match="window"):
        WindowSummarizer(
            Msg, Summary, summarize=_offline_summarize, build=_build, window=0
        )
    with pytest.raises(ValueError, match="every"):
        WindowSummarizer(
            Msg, Summary, summarize=_offline_summarize, build=_build, every=0
        )
    with pytest.raises(ValueError, match="keep"):
        WindowPruner(Msg, keep=-1)
    with pytest.raises(ValueError, match="window"):
        RollingDigestSummarizer(
            Msg, Digest, summarize=_offline_digest, build=_build_digest, window=-1
        )
    with pytest.raises(ValueError, match="trigger"):
        RollingDigestSummarizer(
            Msg, Digest, summarize=_offline_digest, build=_build_digest, trigger=0
        )
    with pytest.raises(ValueError, match="trigger"):
        RollingDigestSummarizer(
            Msg,
            Digest,
            summarize=_offline_digest,
            build=_build_digest,
            window=8,
            trigger=4,
        )
    with pytest.raises(ValueError, match="digest_id"):
        RollingDigestSummarizer(
            Msg, Digest, summarize=_offline_digest, build=_build_digest, digest_id=""
        )
    with pytest.raises(TypeError, match="BaseModel"):
        WindowPruner([Msg, "not-a-type"], keep=1)  # type: ignore[list-item]


def _digest_flow(*, window: int = 4, trigger: int = 6) -> Agent:
    return Agent(
        "digest",
        consumes=[Consume(Msg)],
        steps=[
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
        runtime = Runtime(ctx, groups=[_digest_flow(window=window, trigger=trigger)])
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
        agent = Agent(
            "digest-multi",
            consumes=[Consume(Question), Consume(FinalResponse)],
            steps=[
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
        runtime = Runtime(ctx, groups=[agent])
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


def test_pruner_bounds_multiple_message_types():
    async def _arun() -> Context:
        ctx = Context()
        agent = Agent(
            "prune-multi",
            consumes=[Consume(Question), Consume(FinalResponse)],
            steps=[WindowPruner([Question, FinalResponse], keep=4)],
        )
        runtime = Runtime(ctx, groups=[agent])
        for i in range(10):
            if i % 2 == 0:
                ctx.create(Question(text=f"q{i}"))
            else:
                ctx.create(FinalResponse(text=f"r{i}"))
            await runtime.arun()
        return ctx

    ctx = asyncio.run(_arun())
    total = len(ctx.list_artifacts(Question)) + len(ctx.list_artifacts(FinalResponse))
    assert total == 4


def test_summarizer_merges_multiple_message_types():
    async def _arun() -> Context:
        ctx = Context()
        agent = Agent(
            "summarize-multi",
            consumes=[Consume(Question), Consume(FinalResponse)],
            steps=[
                WindowSummarizer(
                    [Question, FinalResponse],
                    Summary,
                    summarize=_offline_summarize,
                    build=_build,
                    window=4,
                    every=2,
                )
            ],
        )
        runtime = Runtime(ctx, groups=[agent])
        for i in range(4):
            if i % 2 == 0:
                ctx.create(Question(text=f"q{i}"))
            else:
                ctx.create(FinalResponse(text=f"r{i}"))
            await runtime.arun()
        return ctx

    ctx = asyncio.run(_arun())
    summaries = sorted(ctx.list_artifacts(Summary), key=lambda s: s.data.round)
    assert [s.data.round for s in summaries] == [1, 2]
