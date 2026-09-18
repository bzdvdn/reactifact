import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, Runtime
from reactifact.budget import Budget


class Evidence(BaseModel):
    text: str


class Marker(BaseModel):
    tag: str


class Collected(BaseModel):
    texts: list[str]


class RecordCollected(Produce[Collected]):
    artifact_type = Collected

    async def produce(self, call):
        self.effects.create(
            Collected(
                texts=sorted(
                    i.data.text for i in call.inputs if isinstance(i.data, Evidence)
                )
            )
        )


class DebouncedCollector(Agent):
    consumes = [Consume(Evidence, debounce=True)]
    produces = [RecordCollected()]


class NonDebouncedCollector(Agent):
    consumes = [Consume(Evidence)]
    produces = [RecordCollected()]


class MixedAgent(Agent):
    """Evidence is debounced, Marker isn't — proves debounce is scoped to
    the `Consume` it's set on, not the whole agent."""

    consumes = [Consume(Evidence, debounce=True), Consume(Marker)]
    produces = [RecordCollected()]


def test_debounce_collapses_multiple_events_into_one_run():
    """A fan-out step creating three Evidence artifacts in one batch (before
    the next generation drains events) wakes the agent once, not three
    times — and the single run sees all three via `inputs`, not just the
    last one via `event`."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[DebouncedCollector()])

    ctx.create(Evidence(text="a"))
    ctx.create(Evidence(text="b"))
    ctx.create(Evidence(text="c"))
    runs = asyncio.run(runtime.arun_once())

    assert runs == 1
    collected = ctx.list_artifacts(Collected)
    assert len(collected) == 1
    assert collected[0].data.texts == ["a", "b", "c"]


def test_without_debounce_each_event_runs_separately():
    """Baseline: same setup, no `debounce=True` — proves the collapsing
    above is what `debounce` does, not some other effect of the batch."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[NonDebouncedCollector()])

    ctx.create(Evidence(text="a"))
    ctx.create(Evidence(text="b"))
    runs = asyncio.run(runtime.arun_once())

    assert runs == 2
    assert len(ctx.list_artifacts(Collected)) == 2


def test_debounce_is_scoped_to_its_own_consume_not_the_whole_agent():
    ctx = Context()
    runtime = Runtime(ctx, agents=[MixedAgent()])

    ctx.create(Evidence(text="a"))
    ctx.create(Evidence(text="b"))
    ctx.create(Marker(tag="m1"))
    ctx.create(Marker(tag="m2"))
    runs = asyncio.run(runtime.arun_once())

    # the two Evidence events collapse into one run; the two Marker events
    # each run separately (not debounced) -> 1 + 2 = 3
    assert runs == 3


def test_debounced_run_counts_as_one_against_max_runs_budget():
    """The whole point of debouncing: three events must cost one run
    against `max_runs`, not three."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[DebouncedCollector()])

    ctx.create(Evidence(text="a"))
    ctx.create(Evidence(text="b"))
    ctx.create(Evidence(text="c"))
    runs = asyncio.run(runtime.arun_once(Budget(max_runs=1)))

    assert runs == 1
    assert len(ctx.list_artifacts(Collected)) == 1
