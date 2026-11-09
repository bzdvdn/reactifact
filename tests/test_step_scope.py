import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Runtime
from reactifact.step import Latest, Step


class Question(BaseModel):
    query_id: str
    text: str


class Doc(BaseModel):
    query_id: str
    text: str


class Answer(BaseModel):
    query_id: str
    text: str


class Compose(Step):
    consumes = (Question, Doc)
    produces = (Answer,)
    scope = "query_id"

    async def run(self, question: Question, docs: list[Doc]) -> None:
        self.effects.upsert(
            Answer(query_id=question.query_id, text="+".join(d.text for d in docs)),
            id=f"answer:{question.query_id}",
        )


class ComposeAgent(Agent):
    steps = [Compose()]


def test_typed_inputs_are_scoped_to_the_trigger_query_id():
    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Doc(query_id="q1", text="a"))
    context.create(Doc(query_id="q2", text="b"))
    context.create(Doc(query_id="q1", text="c"))

    asyncio.run(Runtime(context, agents=[ComposeAgent()]).arun())

    answers = {a.data.query_id: a.data.text for a in context.list_artifacts(Answer)}
    assert answers["q1"] == "a+c"
    assert answers["q2"] == "b"


class Related(Step):
    consumes = (Question,)
    produces = (Answer,)
    scope = "query_id"

    async def run(self, question: Question) -> None:
        docs = self.related_all(Doc)
        self.effects.upsert(
            Answer(
                query_id=question.query_id, text="+".join(d.data.text for d in docs)
            ),
            id=f"answer:{question.query_id}",
        )


class RelatedAgent(Agent):
    steps = [Related()]


def test_related_returns_only_same_scope_artifacts():
    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Doc(query_id="q1", text="a"))
    context.create(Doc(query_id="q2", text="b"))
    context.create(Doc(query_id="q1", text="c"))

    asyncio.run(Runtime(context, agents=[RelatedAgent()]).arun())

    answers = {a.data.query_id: a.data.text for a in context.list_artifacts(Answer)}
    assert answers["q1"] == "a+c"
    assert answers["q2"] == "b"


class Extract(Step):
    consumes = (Question,)
    produces = (Answer,)
    scope = "query_id"

    async def run(
        self,
        call,
        question: Question,
        latest: Latest[Doc],
    ) -> Answer:
        assert call.scope == question.query_id
        return Answer(
            query_id=question.query_id,
            text=f"{question.text}:{latest.text if latest else 'none'}",
        )


def test_latest_and_call_scope_are_correlated_to_the_trigger():
    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Doc(query_id="q1", text="a"))
    context.create(Doc(query_id="q2", text="b"))
    context.create(Doc(query_id="q1", text="c"))

    asyncio.run(
        Runtime(context, agents=[Agent(name="extract", steps=[Extract()])]).arun()
    )

    answers = {a.data.query_id: a.data.text for a in context.list_artifacts(Answer)}
    assert answers["q1"] == "one:c"
    assert answers["q2"] == "two:b"


class CursorStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, call, question: Question) -> None:
        self.effects.upsert(
            Answer(
                query_id=question.query_id,
                text=str(call.cursor.get(Question, -1)),
            ),
            id="cursor-answer",
        )


def test_call_cursor_exposes_input_versions():
    context = Context()
    question = context.create(Question(query_id="q1", text="one"))
    runtime = Runtime(context, agents=[Agent(name="cursor", steps=[CursorStep()])])

    asyncio.run(runtime.arun())
    assert context.get("cursor-answer").data.text == "0"

    context.update(question.id, Question(query_id="q1", text="two"))
    asyncio.run(runtime.arun())
    assert context.get("cursor-answer").data.text == "1"
