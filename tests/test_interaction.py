"""Human/Outbox/Progress collaborators keep data writes and UI signals typed."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from pydantic import BaseModel
from reactifact import (
    Context,
    FakeLLM,
    Human,
    LLMProvider,
    Outbox,
    PendingAction,
    PendingQuestion,
    Pipeline,
    PipelineRunner,
    Progress,
    Runtime,
    RuntimeResources,
    Step,
    step,
)
from reactifact.streaming import ProgressEvent


class Ticket(BaseModel):
    id: str
    text: str


class Approval(BaseModel):
    approved: bool


class Answer(BaseModel):
    text: str


@step(produces=PendingQuestion, reads=Ticket)
def approve(ticket: Ticket, human: Human) -> None:
    human.ask("Approve?", schema=Approval, id=f"approve:{ticket.id}")


@step(produces=PendingAction, reads=Ticket)
def notify(ticket: Ticket, outbox: Outbox) -> None:
    outbox.act("notify", key=f"notify:{ticket.id}", payload={"id": ticket.id})


@step(produces=PendingQuestion, reads=Ticket)
async def approve_call(call) -> None:
    call.human.ask("Approve?", schema=Approval, id=f"call:{call.trigger.id}")


@step(produces=Answer, reads=Ticket)
def answer(ticket: Ticket, progress: Progress) -> Answer:
    progress("Считаю…", source="test")
    progress("Нашёл 2", count=2)
    return Answer(text=ticket.text)


@step(produces=Answer, reads=Ticket)
async def answer_call(call) -> Answer:
    call.progress("call progress", tool="lookup")
    return Answer(text=call.trigger.data.text)


def test_human_creates_a_pending_question():
    result = PipelineRunner(Pipeline(steps=[approve])).run(
        Ticket(id="42", text="refund")
    )
    questions = result.context.pending_questions()
    assert len(questions) == 1
    assert questions[0].data.resolution_schema


def test_outbox_records_a_pending_action():
    result = PipelineRunner(Pipeline(steps=[notify])).run(Ticket(id="7", text="x"))
    actions = result.context.pending_actions()
    assert len(actions) == 1
    assert actions[0].data.idempotency_key == "notify:7"


def test_stepcall_exposes_human():
    result = PipelineRunner(Pipeline(steps=[approve_call])).run(
        Ticket(id="9", text="x")
    )
    assert len(result.context.pending_questions()) == 1


def _statuses(events: Iterable[ProgressEvent]) -> list[ProgressEvent]:
    return [event for event in events if event.kind == "status"]


def test_progress_publishes_announcements_with_payload() -> None:
    runner = PipelineRunner(Pipeline(steps=[answer]))
    events = asyncio.run(_collect(runner.astream(Ticket(id="1", text="x"))))

    statuses = _statuses(events)
    assert [event.message for event in statuses] == ["Считаю…", "Нашёл 2"]
    assert statuses[0].data == {"source": "test"}
    assert statuses[1].data == {"count": 2}


def test_stepcall_exposes_progress() -> None:
    runner = PipelineRunner(Pipeline(steps=[answer_call]))
    events = asyncio.run(_collect(runner.astream(Ticket(id="2", text="x"))))

    statuses = _statuses(events)
    assert [event.message for event in statuses] == ["call progress"]
    assert statuses[0].data == {"tool": "lookup"}


async def _collect(events: AsyncIterator[ProgressEvent]) -> list[ProgressEvent]:
    return [event async for event in events]


class UsesLLM(Step):
    reads = (Ticket,)
    produces = (Answer,)

    async def run(self, ticket: Ticket, llm: LLMProvider | None) -> Answer:
        assert llm is not None
        return Answer(text="ok")


def test_optional_service_injection_resolves_a_provider() -> None:
    context = Context(resources=RuntimeResources(llm=FakeLLM("{}")))
    context.create(Ticket(id="3", text="x"))

    asyncio.run(Runtime(context, steps=[UsesLLM()]).arun())

    assert context.list_artifacts(Answer)[0].data.text == "ok"


class Prefs(BaseModel):
    out_dir: str = "/tmp"


class UsesPrefs(Step):
    reads = (Ticket,)
    produces = (Answer,)

    async def run(self, ticket: Ticket, prefs: Prefs) -> Answer:
        return Answer(text=prefs.out_dir)


class UsesOptionalPrefs(Step):
    reads = (Ticket,)
    produces = (Answer,)

    async def run(self, ticket: Ticket, prefs: Prefs | None) -> Answer:
        assert prefs is not None
        return Answer(text=prefs.out_dir)


class ConsumesPrefsArtifact(Step):
    reads = (Prefs,)
    produces = (Answer,)

    async def run(self, prefs: Prefs) -> Answer:
        return Answer(text=prefs.out_dir)


def test_registered_pydantic_model_is_injected() -> None:
    resources = RuntimeResources()
    resources.register(Prefs, Prefs(out_dir="/resource"))
    context = Context(resources=resources)
    context.create(Ticket(id="4", text="x"))

    asyncio.run(Runtime(context, steps=[UsesPrefs()]).arun())

    assert context.list_artifacts(Answer)[0].data.text == "/resource"


def test_registered_pydantic_model_is_injected_when_optional() -> None:
    resources = RuntimeResources()
    resources.register(Prefs, Prefs(out_dir="/optional"))
    context = Context(resources=resources)
    context.create(Ticket(id="5", text="x"))

    asyncio.run(Runtime(context, steps=[UsesOptionalPrefs()]).arun())

    assert context.list_artifacts(Answer)[0].data.text == "/optional"


def test_unregistered_pydantic_model_still_resolves_as_artifact() -> None:
    context = Context()
    context.create(Prefs(out_dir="/artifact"))

    asyncio.run(Runtime(context, steps=[ConsumesPrefsArtifact()]).arun())

    assert context.list_artifacts(Answer)[0].data.text == "/artifact"


def test_registered_pydantic_model_wins_over_an_artifact() -> None:
    resources = RuntimeResources()
    resources.register(Prefs, Prefs(out_dir="/resource"))
    context = Context(resources=resources)
    context.create(Prefs(out_dir="/artifact"))

    asyncio.run(Runtime(context, steps=[ConsumesPrefsArtifact()]).arun())

    assert context.list_artifacts(Answer)[0].data.text == "/resource"
