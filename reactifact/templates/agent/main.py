"""A minimal reactifact agent: one typed step, no web, no API key.

Run it with the CLI (and get a trace):

    reactifact run main:pipeline "hello"

Or directly:

    python main.py
"""

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


pipeline = Pipeline(
    steps=[answer],
    seed_type=Question,
    answer_type=Answer,
)


if __name__ == "__main__":
    result = pipeline.run(Question(text="hello from reactifact"))
    print(result.latest(Answer).data.text)
