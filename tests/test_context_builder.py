import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, Runtime, RuntimeResources
from reactifact.context_builder import HeuristicTokenCounter, TokenBudgetContextBuilder


class Item(BaseModel):
    text: str


class Seen(BaseModel):
    texts: list[str]


class RecordSeen(Produce[Seen]):
    artifact_type = Seen

    async def produce(self, context, inputs, event=None):
        self.effects.create(Seen(texts=[i.data.text for i in inputs]))


class Collector(Agent):
    consumes = [Consume(Item)]
    produces = [RecordSeen()]


def test_heuristic_counter_overestimates_and_is_never_zero_for_nonempty_text():
    counter = HeuristicTokenCounter()
    assert counter.count("") == 0
    assert counter.count("x") >= 1
    # ~3.5 chars/token, not the more generous ~4 — a deliberately conservative estimate
    assert counter.count("x" * 35) >= 10


def test_no_builder_keeps_every_matching_artifact():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Collector()])
    ctx.create(Item(text="a"))
    asyncio.run(runtime.arun())
    ctx.create(Item(text="b"))
    asyncio.run(runtime.arun())

    seen = ctx.list_artifacts(Seen)[-1]
    assert sorted(seen.data.texts) == ["a", "b"]


def test_token_budget_keeps_prefix_by_recency():
    builder = TokenBudgetContextBuilder(max_tokens=5)
    ctx = Context(resources=RuntimeResources(context_builder=builder))
    runtime = Runtime(ctx, agents=[Collector()])

    ctx.create(Item(text="x" * 50))
    asyncio.run(runtime.arun())
    ctx.create(Item(text="y" * 50))
    asyncio.run(runtime.arun())

    # both items are 50 chars (way over a 5-token budget); only the newest
    # (y) is kept, the older (x) is truncated away
    seen = ctx.list_artifacts(Seen)[-1]
    assert seen.data.texts == ["y" * 50]


def test_token_budget_never_drops_to_zero_inputs():
    builder = TokenBudgetContextBuilder(max_tokens=1)
    ctx = Context(resources=RuntimeResources(context_builder=builder))
    runtime = Runtime(ctx, agents=[Collector()])

    ctx.create(Item(text="a single item way over budget on its own"))
    asyncio.run(runtime.arun())

    seen = ctx.list_artifacts(Seen)[-1]
    assert len(seen.data.texts) == 1


def test_provenance_reads_match_the_truncated_inputs():
    """Runtime._collect_reads and Agent._collect_inputs must agree: the
    commit's recorded reads reflect what the builder actually let through,
    not the pre-truncation candidate set."""
    builder = TokenBudgetContextBuilder(max_tokens=5)
    ctx = Context(resources=RuntimeResources(context_builder=builder))
    runtime = Runtime(ctx, agents=[Collector()])

    ctx.create(Item(text="x" * 50))
    asyncio.run(runtime.arun())
    ctx.create(Item(text="y" * 50))
    asyncio.run(runtime.arun())

    last_commit = ctx.commit_log()[-1]
    # one read for the triggering artifact (y) + the builder kept only y as
    # input too, deduped by Runtime._collect_reads -> exactly one read
    assert len(last_commit.reads) == 1


def test_custom_rank_key_overrides_recency():
    # build() always ranks highest-key-first (reverse=True, same convention
    # as the recency default where "newest" is the largest datetime) — so
    # "prefer the shortest text" is expressed as the negated length.
    builder = TokenBudgetContextBuilder(
        max_tokens=5,
        rank_key=lambda a: -len(a.data.text),
    )
    ctx = Context(resources=RuntimeResources(context_builder=builder))
    runtime = Runtime(ctx, agents=[Collector()])

    ctx.create(Item(text="short"))
    asyncio.run(runtime.arun())
    ctx.create(Item(text="a much longer piece of text"))
    asyncio.run(runtime.arun())

    seen = ctx.list_artifacts(Seen)[-1]
    assert seen.data.texts == ["short"]
