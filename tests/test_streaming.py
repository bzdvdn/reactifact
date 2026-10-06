import asyncio
import contextlib

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime
from reactifact.streaming import EventHub, ProgressEvent


class UserMsg(BaseModel):
    text: str


class Done(BaseModel):
    text: str


class Announcer(Agent):
    consumes = [Consume(UserMsg)]

    async def run(self, event, context):
        context.announce("Думаю над вопросом...", kind="status", source="brain")
        context.announce("Найдено 3 соответствия", kind="status")
        return Patch().create(Done(text="ok"))


class Slow(Agent):
    consumes = [Consume(UserMsg)]

    async def run(self, event, context):
        await asyncio.sleep(30)  # stays running while the consumer goes away
        return Patch().create(Done(text="ok"))


async def collect_events(runtime):
    return [ev async for ev in runtime.astream()]


def test_astream_yields_status_and_run_end():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Announcer()])
    ctx.create(UserMsg(text="привет"))

    events = asyncio.run(collect_events(runtime))

    assert events[0].kind == "run_start"
    assert events[-1].kind == "run_end"
    statuses = [e for e in events if e.kind == "status"]
    assert [e.message for e in statuses] == [
        "Думаю над вопросом...",
        "Найдено 3 соответствия",
    ]
    assert statuses[0].data["source"] == "brain"
    assert events[-1].data["runs"] >= 1
    assert events[-1].data["outcome"] in {"completed", "budget_runs_exceeded"}


def test_announce_is_noop_without_subscribers():
    ctx = Context()
    ctx.announce("никто не слушает", kind="status")  # must not raise
    assert isinstance(ctx._hub, EventHub)


def test_astream_leaves_no_pending_tasks_after_consuming():
    """Regression: the runner used to leave a cancelled-but-unawaited
    ``queue.get()``/``done.wait()`` future pending, which surfaced as
    "Task was destroyed but it is pending!" when the event loop closed."""

    async def run():
        ctx = Context()
        runtime = Runtime(ctx, agents=[Announcer()])
        ctx.create(UserMsg(text="привет"))
        async for _ in runtime.astream():
            pass
        await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(run()) == []


def test_astream_close_while_waiting_leaves_no_pending_tasks():
    """A dropped consumer closed while the stream is awaiting
    ``queue.get()``/``done.wait()`` must not leave those futures pending."""

    async def run():
        ctx = Context()
        runtime = Runtime(ctx, agents=[Slow()])
        ctx.create(UserMsg(text="привет"))
        agen = runtime.astream()
        assert (await agen.__anext__()).kind == "run_start"

        puller = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0)  # enter the wait: queue.get()/done.wait() created
        puller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await puller
        await agen.aclose()
        for _ in range(10):
            await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(run()) == []


def test_eventhub_multiple_subscribers():
    hub = EventHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()

    hub.publish(ProgressEvent(kind="status", message="x"))
    ev1 = q1.get_nowait()
    ev2 = q2.get_nowait()
    assert ev1.message == ev2.message == "x"

    hub.unsubscribe(q1)
    hub.unsubscribe(q2)
    assert not hub.has_subscribers
    hub.publish(ProgressEvent(kind="status", message="y"))
    assert q2.empty()
