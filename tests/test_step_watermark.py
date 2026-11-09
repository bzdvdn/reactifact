import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Event, EventType
from reactifact.effects import Effects, reset_effects, set_effects
from reactifact.step import Step


class A(BaseModel):
    text: str


class B(BaseModel):
    text: str


class Out(BaseModel):
    text: str


runs: dict[str, int] = {"step": 0}


class StepAB(Step):
    consumes = (A, B)
    produces = (Out,)

    async def run(self, a: A, b: B) -> Out:
        runs["step"] += 1
        return Out(text=f"{a.text}:{b.text}")


class Flow(Agent):
    steps = [StepAB()]


def test_watermark_skips_a_repeated_event_with_unchanged_inputs():
    runs["step"] = 0
    context = Context()
    a = context.create(A(text="a"))
    context.create(B(text="b"))
    agent = Flow()

    event = Event(EventType.ARTIFACT_CREATED, A, a.id)

    def execute() -> None:
        token = set_effects(Effects(context))
        try:
            asyncio.run(agent.execute(context, event))
        finally:
            reset_effects(token)

    execute()
    assert runs["step"] == 1

    # The same event again: no input changed since the cursor -> skip.
    execute()
    assert runs["step"] == 1

    # A real update bumps the version -> the step runs again.
    context.update(a.id, A(text="a2"))
    execute()
    assert runs["step"] == 2


def test_watermark_cursors_survive_context_round_trip():
    runs["step"] = 0
    context = Context()
    a = context.create(A(text="a"))
    context.create(B(text="b"))
    agent = Flow()
    event = Event(EventType.ARTIFACT_CREATED, A, a.id)

    def execute(target: Context) -> None:
        token = set_effects(Effects(target))
        try:
            asyncio.run(agent.execute(target, event))
        finally:
            reset_effects(token)

    execute(context)
    assert runs["step"] == 1

    restored = Context.from_dict(context.to_dict())
    execute(restored)
    assert runs["step"] == 1  # same cursor restored -> no re-run
