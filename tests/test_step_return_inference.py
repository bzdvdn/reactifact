"""Return annotations as the output contract (FastAPI-style)."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import DERIVED_FROM, Context, Runtime, step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Note(BaseModel):
    text: str


@step(consumes=Question)
def inferred(question: Question) -> Answer:
    return Answer(text=question.text)


@step(consumes=Question)
def pair(question: Question) -> tuple[Answer, Note]:
    return Answer(text=question.text), Note(text=question.text.upper())


@step(consumes=Question)
def many(question: Question) -> list[Note]:
    return [Note(text="a"), Note(text="b")]


def run(steps: list, seed: str = "hi") -> Context:
    context = Context()
    context.create(Question(text=seed), id="q")
    Runtime(context, steps=steps).run()
    return context


def test_return_annotation_is_the_output_contract():
    assert inferred.produces == (Answer,)
    assert pair.produces == (Answer, Note)
    assert many.produces == (Note,)
    assert inferred.once is True
    assert inferred.provenance == DERIVED_FROM


def test_tuple_return_creates_every_artifact():
    context = run([pair])
    assert context.latest(Answer) is not None
    assert context.latest(Note) is not None


def test_list_return_creates_every_item():
    context = run([many])
    assert [note.data.text for note in context.list_artifacts(Note)] == ["a", "b"]


def test_inferred_return_is_idempotent_for_the_same_trigger():
    context = run([inferred])
    first = context.latest(Answer)
    assert first is not None

    context.touch("q")
    Runtime(context, steps=[inferred]).run()
    answers = context.list_artifacts(Answer)
    assert len(answers) == 1
    assert answers[0].id == first.id


def test_inferred_step_links_its_inputs_by_default():
    context = run([inferred])
    answer = context.latest(Answer)
    assert answer is not None
    assert [a.id for a in context.related(answer.id, DERIVED_FROM)] == ["q"]
