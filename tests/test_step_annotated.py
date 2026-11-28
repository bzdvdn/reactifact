import asyncio
from typing import Annotated

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    Effects,
    Runtime,
    Wakes,
    When,
    step,
)
from reactifact.agent import Step


class Ticket(BaseModel):
    status: str
    text: str


class Out(BaseModel):
    text: str


class PickOpen(Step):
    produces = (Out,)

    async def run(
        self,
        ticket: Annotated[Ticket, When(lambda t: t.status == "open")],
        effects: Effects,
    ) -> None:
        effects.upsert(Out(text=ticket.text), id=f"out:{ticket.text}")


class PickAgent(Agent):
    steps = [PickOpen()]


def test_annotated_when_derives_consumes_and_filters_inputs():
    context = Context()
    context.create(Ticket(status="open", text="open-1"))
    context.create(Ticket(status="closed", text="closed-1"))
    context.create(Ticket(status="open", text="open-2"))

    asyncio.run(Runtime(context, agents=[PickAgent()]).arun())

    assert {o.data.text for o in context.list_artifacts(Out)} == {"open-1", "open-2"}


class DeclaredConsume(Step):
    reads = (Annotated[Ticket, When(lambda t: t.status == "open")],)
    produces = (Out,)

    async def run(self, ticket: Ticket, effects: Effects) -> None:
        effects.upsert(Out(text=ticket.text), id=f"declared:{ticket.text}")


class DeclaredAgent(Agent):
    steps = [DeclaredConsume()]


def test_annotated_when_works_in_explicit_consumes():
    context = Context()
    context.create(Ticket(status="open", text="yes"))
    context.create(Ticket(status="closed", text="no"))

    asyncio.run(Runtime(context, agents=[DeclaredAgent()]).arun())

    assert {o.data.text for o in context.list_artifacts(Out)} == {"yes"}


class History(BaseModel):
    text: str


class WithHistory(Step):
    produces = (Out,)

    async def run(
        self,
        ticket: Ticket,
        history: Annotated[History, Wakes(False)],
        effects: Effects,
    ) -> None:
        effects.upsert(Out(text=f"{ticket.text}:{history.text}"), id="with-history")


def test_wakes_false_annotation_reads_without_waking():
    step = WithHistory()
    specs = {spec.artifact_type: spec.wakes for spec in step.read_specs() or ()}
    assert specs[Ticket] is True
    assert specs[History] is False

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(History(text="h"))

    asyncio.run(
        Runtime(context, agents=[Agent(name="with-history", steps=[step])]).arun()
    )
    assert context.latest(Out).data.text == "q:h"


@step(produces=Out)
async def typed_function_step(ticket: Ticket, effects: Effects) -> None:
    effects.upsert(Out(text=f"typed:{ticket.text}"), id="typed-function")


def test_step_decorator_accepts_typed_parameters():
    step = typed_function_step
    specs = {spec.artifact_type: spec.wakes for spec in step.read_specs() or ()}
    assert specs[Ticket] is True

    context = Context()
    context.create(Ticket(status="open", text="hello"))
    asyncio.run(Runtime(context, agents=[Agent(name="typed", steps=[step])]).arun())
    assert context.latest(Out).data.text == "typed:hello"


class ReadsWithoutConsume(Step):
    """Explicit consumes = wake set; a plain typed param = read-only input."""

    reads = (Ticket,)
    produces = (Out,)

    async def run(
        self,
        ticket: Ticket,
        history: History,
        effects: Effects,
    ) -> None:
        effects.upsert(Out(text=f"{ticket.text}:{history.text}"), id="reads-history")


def test_explicit_consumes_adds_missing_typed_params_as_reads():
    step = ReadsWithoutConsume()
    specs = {spec.artifact_type: spec.wakes for spec in step.read_specs() or ()}
    assert specs[Ticket] is True
    assert specs[History] is False

    context = Context()
    context.create(Ticket(status="open", text="q"))
    history = context.create(History(text="h"))
    asyncio.run(Runtime(context, agents=[Agent(name="reads", steps=[step])]).arun())
    assert context.latest(Out).data.text == "q:h"

    # A read-only input never wakes the step on its own.
    context.update(history.id, History(text="h2"))
    asyncio.run(Runtime(context, agents=[Agent(name="reads", steps=[step])]).arun())
    assert context.latest(Out).data.text == "q:h"


def test_function_step_explicit_consumes_adds_reads():
    runs: list[str] = []

    @step(produces=Out, reads=Ticket)
    async def read_history(
        ticket: Ticket,
        history: History,
        effects: Effects,
    ) -> None:
        runs.append(history.text)
        effects.upsert(Out(text=f"fn:{ticket.text}:{history.text}"), id="fn-reads")

    specs = {spec.artifact_type: spec.wakes for spec in read_history.read_specs() or ()}
    assert specs[Ticket] is True
    assert specs[History] is False

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(History(text="h"))
    asyncio.run(
        Runtime(context, agents=[Agent(name="fn", steps=[read_history])]).arun()
    )
    assert context.latest(Out).data.text == "fn:q:h"
    assert runs == ["h"]


def test_explicit_consumes_preserves_when_on_auto_reads():
    class ReadMatching(Step):
        reads = (Ticket,)
        produces = (Out,)

        async def run(
            self,
            ticket: Ticket,
            history: Annotated[History, When(lambda h: h.text == "yes")],
            effects: Effects,
        ) -> None:
            effects.upsert(Out(text=history.text), id="matching-history")

    step = ReadMatching()
    history_spec = next(
        spec for spec in step.read_specs() or () if spec.artifact_type is History
    )
    assert history_spec.wakes is False
    assert history_spec.condition is not None

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(History(text="no"))
    context.create(History(text="yes"))
    asyncio.run(Runtime(context, agents=[Agent(name="when", steps=[step])]).arun())
    assert context.latest(Out).data.text == "yes"


def test_explicit_consumes_does_not_duplicate_a_declared_read():
    class Declared(Step):
        reads = (Ticket, Consume(History, wakes=False))
        produces = (Out,)

        async def run(
            self,
            ticket: Ticket,
            history: History,
            effects: Effects,
        ) -> None:
            effects.upsert(Out(text=history.text), id="declared-read")

    specs = [
        spec for spec in Declared().read_specs() or () if spec.artifact_type is History
    ]
    assert len(specs) == 1
    assert specs[0].wakes is False


def test_explicit_superclass_does_not_duplicate_a_typed_subclass():
    class Base(BaseModel):
        text: str

    class Child(Base):
        pass

    class Covers(Step):
        reads = (Ticket, Consume(Base))
        produces = (Out,)

        async def run(
            self,
            ticket: Ticket,
            child: Child,
            effects: Effects,
        ) -> None:
            effects.upsert(Out(text=child.text), id="covered-child")

    step = Covers()
    assert not [spec for spec in step.read_specs() or () if spec.artifact_type is Child]

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(Child(text="c"))
    asyncio.run(Runtime(context, agents=[Agent(name="covered", steps=[step])]).arun())
    assert context.latest(Out).data.text == "c"


class ArtifactParams(Step):
    produces = (Out,)

    async def run(
        self,
        ticket: Artifact[Ticket],
        history: Artifact[History],
        effects: Effects,
    ) -> None:
        effects.upsert(
            Out(text=f"{ticket.data.text}:{history.data.text}"),
            id="artifact-params",
        )


def test_artifact_params_derive_consumes():
    step = ArtifactParams()
    specs = {spec.artifact_type: spec.wakes for spec in step.read_specs() or ()}
    assert specs == {Ticket: True, History: True}

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(History(text="h"))
    asyncio.run(Runtime(context, agents=[Agent(name="artifact", steps=[step])]).arun())
    assert context.latest(Out).data.text == "q:h"


class Note(BaseModel):
    text: str


class ReadsOptionalArtifact(Step):
    reads = (Ticket,)
    produces = (Out,)

    async def run(
        self,
        ticket: Ticket,
        note: Artifact[Note] | None,
        effects: Effects,
    ) -> None:
        if note is None:
            effects.upsert(Out(text="none"), id="optional-artifact")
        else:
            effects.upsert(
                Out(text=f"{note.id}:{note.data.text}"), id="optional-artifact"
            )


def test_optional_artifact_handle_is_none_when_absent():
    step = ReadsOptionalArtifact()
    specs = {spec.artifact_type: spec.wakes for spec in step.read_specs() or ()}
    assert specs[Note] is False  # auto-added read-only, not a trigger

    context = Context()
    context.create(Ticket(status="open", text="q"))
    asyncio.run(Runtime(context, agents=[Agent(name="opt", steps=[step])]).arun())
    assert context.latest(Out).data.text == "none"


def test_optional_artifact_handle_returns_the_artifact():
    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(Note(text="h"), id="note:1")

    asyncio.run(
        Runtime(
            context, agents=[Agent(name="opt", steps=[ReadsOptionalArtifact()])]
        ).arun()
    )

    assert context.latest(Out).data.text == "note:1:h"
