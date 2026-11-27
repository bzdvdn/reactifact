"""A minimal reactifact agent: one typed step, no web, no API key.

Run it with the CLI (and get a trace):

    reactifact run main:pipeline "hello"

Or directly:

    python main.py
"""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Agent, Consume, Pipeline, PipelineRunner, step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


pipeline = Pipeline(
    agents=[Agent("answer", consumes=[Consume(Question)], steps=[answer])],
    seed_type=Question,
    answer_type=Answer,
)


if __name__ == "__main__":
    result = PipelineRunner(pipeline).run(Question(text="hello from reactifact"))
    print(result.latest(Answer).data.text)
