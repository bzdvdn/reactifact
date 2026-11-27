"""0.25 ergonomics: scope=, Unscoped, CurrentRequest, side-effect steps, TurnContext."""

from __future__ import annotations

import asyncio
from typing import Annotated

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    CurrentRequest,
    Effects,
    Latest,
    Runtime,
    Step,
    StepCall,
    Unscoped,
    step,
)
from reactifact.recipes.conversation import TurnContext, TurnContextStep


class Question(BaseModel):
    query_id: str = ""
    text: str


class Doc(BaseModel):
    query_id: str = ""
    text: str


class Answer(BaseModel):
    query_id: str = ""
    text: str


class Recall(BaseModel):
    query_id: str
    texts: list[str]


class Digest(BaseModel):
    query_id: str = ""
    text: str


def test_function_step_accepts_scope_and_unscoped_history():
    seen: list[list[str]] = []

    @step(
        consumes=Question,
        produces=Answer,
        scope="query_id",
    )
    async def compose(
        question: Question,
        docs: list[Doc],
        answers: Annotated[list[Artifact[Answer]], Unscoped()],
    ) -> Answer:
        seen.append([a.data.text for a in answers])
        return Answer(
            query_id=question.query_id,
            text="+".join(doc.text for doc in docs),
        )

    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Doc(query_id="q1", text="a"))
    context.create(Doc(query_id="q2", text="b"))
    context.create(Answer(query_id="q1", text="old-answer"))

    asyncio.run(Runtime(context, steps=[compose]).arun())

    answers = {a.data.query_id: a.data.text for a in context.list_artifacts(Answer)}
    assert answers["q1"] == "a"
    assert answers["q2"] == "b"
    # The unscoped history saw the old answer on both turns.
    assert seen and all("old-answer" in texts for texts in seen)


def test_step_constructor_accepts_scope():
    class Compose(Step[Answer]):
        consumes = (Question, Doc)
        deletes = ()

        async def run(
            self, question: Question, docs: list[Doc], effects: Effects
        ) -> None:
            effects.create(
                Answer(
                    query_id=question.query_id,
                    text="+".join(doc.text for doc in docs),
                )
            )

    compose = Compose(scope="query_id")
    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Doc(query_id="q1", text="a"))
    context.create(Doc(query_id="q2", text="b"))

    asyncio.run(Runtime(context, steps=[compose]).arun())

    answers = {a.data.query_id: a.data.text for a in context.list_artifacts(Answer)}
    assert answers == {"q1": "a", "q2": "b"}


def test_current_request_is_injected_by_type():
    @step(consumes=Question, produces=Answer)
    def answer(question: Question, request: CurrentRequest) -> Answer:
        return Answer(text=f"{request['tenant']}:{question.text}")

    context = Context()
    context.create(Question(text="hi"))
    asyncio.run(Runtime(context, steps=[answer]).arun(request={"tenant": "acme"}))
    assert context.latest(Answer).data.text == "acme:hi"


def test_side_effect_only_step_allows_empty_produces_and_reports_deletes():
    class Cleanup(Step):
        produces = ()
        deletes = (Answer,)
        consumes = (Question,)

        async def run(self, context: Context, effects: Effects) -> None:
            for artifact in context.list_artifacts(Answer):
                effects.delete(artifact)

    context = Context()
    context.create(Question(text="go"))
    context.create(Answer(text="old"))
    asyncio.run(Runtime(context, steps=[Cleanup()]).arun())
    assert context.latest(Answer) is None

    from reactifact.contracts import describe_contracts

    report = describe_contracts([Agent(steps=[Cleanup()])]).to_dict()
    step_contract = report["groups"][0]["steps"][0]
    assert step_contract["produces"] == []
    assert step_contract["deletes"] == ["Answer"]


def test_decorated_side_effect_step_can_declare_empty_produces():
    @step(consumes=Question, produces=())
    async def cleanup(call: StepCall) -> None:
        for artifact in call.context.list_artifacts(Answer):
            call.effects.delete(artifact)

    context = Context()
    context.create(Question(text="go"))
    context.create(Answer(text="old"))
    asyncio.run(Runtime(context, steps=[cleanup]).arun())
    assert context.latest(Answer) is None


def test_step_progress_property_publishes_events():
    class ProgressStep(Step[Answer]):
        consumes = (Question,)

        async def run(self, question: Question) -> Answer:
            self.progress("working", count=1)
            return Answer(text=question.text)

    context = Context()
    context.create(Question(text="hi"))
    queue = context.subscribe()
    asyncio.run(Runtime(context, steps=[ProgressStep()]).arun())
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    context.unsubscribe(queue)
    assert any(event.message == "working" for event in events)


def test_turn_context_step_builds_window_previous_query_and_digest():
    class Digest(BaseModel):
        text: str

    context = Context()
    q1 = context.create(Question(text="first question"))
    builder = TurnContextStep(
        Question,
        assistant_type=Answer,
        window=None,
        name="turn_context",
    )
    asyncio.run(Runtime(context, steps=[builder]).arun())
    first = context.get(f"turn:{q1.id}")
    assert first is not None
    assert first.data.previous_query is None

    context.create(Answer(text="first answer"))
    context.create(Digest(text="rolling summary"), id="digest")
    q2 = context.create(Question(text="and second?"))
    asyncio.run(Runtime(context, steps=[builder]).arun())

    current = context.get(f"turn:{q2.id}")
    assert current is not None
    data = current.data
    assert isinstance(data, TurnContext)
    assert data.query == "and second?"
    assert data.previous_query == "first question"
    assert data.digest == "rolling summary"
    assert data.is_follow_up is True
    assert [message.text for message in data.history] == [
        "first question",
        "first answer",
        "and second?",
    ]


def test_turn_context_ignores_messages_after_its_trigger():
    context = Context()
    context.create(Question(text="first question"))
    context.create(Answer(text="first answer"))
    second = context.create(Question(text="second question"))
    context.create(Answer(text="future answer"))

    builder = TurnContextStep(
        Question,
        assistant_type=Answer,
        window=None,
        read=Consume(Question),
    )
    asyncio.run(Runtime(context, steps=[builder]).arun())

    current = context.get(f"turn:{second.id}")
    assert current is not None
    assert [message.text for message in current.data.history] == [
        "first question",
        "first answer",
        "second question",
    ]
    assert current.data.previous_query == "first question"


def test_latest_unscoped_reads_across_turns():
    @step(consumes=Question, produces=Answer, scope="query_id")
    def read_latest(
        question: Question,
        digest: Annotated[Latest[Digest], Unscoped()],
    ) -> Answer:
        return Answer(
            query_id=question.query_id,
            text=digest.text if digest is not None else "none",
        )

    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Digest(query_id="q2", text="other turn"))
    asyncio.run(Runtime(context, steps=[read_latest]).arun())
    assert context.latest(Answer).data.text == "other turn"


def test_chat_reply_projects_the_latest_answer_and_metadata():
    from reactifact.chat import ChatReply

    context = Context()
    answer = context.create(Answer(query_id="q1", text="raw"))
    reply = ChatReply(
        Answer,
        render=lambda data: data.text.upper(),
        extra=lambda artifact: {
            "answer_id": artifact.id,
            "sources": artifact.data.query_id,
        },
        empty={"reply": "empty", "waiting": False},
    )
    payload = reply.build(context, "q1")
    assert payload == {
        "reply": "RAW",
        "waiting": False,
        "answer_id": answer.id,
        "sources": "q1",
    }
    assert reply.build(context, "missing") == {"reply": "empty", "waiting": False}


def test_transcript_state_supports_content_and_artifact_metadata():
    from reactifact.recipes.conversation import MessageSpec, Transcript

    transcript = Transcript(
        [
            MessageSpec(
                Question,
                "user",
                state_field="content",
                artifact_meta=lambda artifact: {
                    "id": artifact.id,
                    "type": "user",
                },
            ),
            MessageSpec(
                Answer,
                "assistant",
                state_field="content",
                artifact_meta=lambda artifact: {
                    "id": artifact.id,
                    "query_id": artifact.data.query_id,
                    "type": "assistant",
                },
            ),
        ]
    )
    context = Context()
    question = context.create(Question(text="hello"))
    answer = context.create(Answer(text="hi"))
    state = transcript.state(context)
    assert state["messages"][0] == {
        "role": "user",
        "content": "hello",
        "at": question.created_at.isoformat(),
        "id": question.id,
        "type": "user",
    }
    assert state["messages"][1]["content"] == "hi"
    assert state["messages"][1]["id"] == answer.id
    assert state["messages"][1]["query_id"] == ""


def test_turn_context_build_receives_prior_artifacts_and_can_ensure():
    from reactifact import OnCreate

    class CustomContext(BaseModel):
        query: str
        prior_question: str
        prior_answer: str

    def build(facts):
        return CustomContext(
            query=facts.query,
            prior_question=facts.previous_query or "",
            prior_answer=(
                facts.previous_answer.data.text
                if facts.previous_answer is not None
                else ""
            ),
        )

    context = Context()
    context.create(Question(text="first"))
    context.create(Answer(text="first answer"))
    builder = TurnContextStep(
        Question,
        assistant_type=Answer,
        artifact_type=CustomContext,
        read=OnCreate(Question),
        ensure=True,
        build=build,
    )
    context.create(Question(text="second"))
    asyncio.run(Runtime(context, steps=[builder]).arun())

    current = context.latest(CustomContext)
    assert current is not None
    assert current.data.query == "second"
    assert current.data.prior_question == "first"
    assert current.data.prior_answer == "first answer"
