"""A tiny Pipeline for `reactifact run` tests."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Pipeline, step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


pipeline = Pipeline(steps=[answer], seed_type=Question, answer_type=Answer)
