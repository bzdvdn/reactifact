"""Experiment/Arena: two variants on one dataset, one winner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from reactifact import Consume, Context, Step, StepGroup
from reactifact.eval.dataset import Dataset
from reactifact.experiments import Experiment


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class UpperStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


class LowerStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.lower())


class UpperAgent(StepGroup):
    consumes = [Consume(Question)]

    def __init__(self) -> None:
        super().__init__(name="upper", steps=[UpperStep()])


class LowerAgent(StepGroup):
    consumes = [Consume(Question)]

    def __init__(self) -> None:
        super().__init__(name="lower", steps=[LowerStep()])


def _seed(context: Context, inputs: Mapping[str, Any]) -> None:
    context.create(Question(text=inputs["question"]))


def _exact(eval_input: Any) -> float:
    expected = (eval_input.example.reference_outputs or {}).get("answer")
    return 1.0 if eval_input.outputs.get("answer") == expected else 0.0


async def run_experiment_scene() -> dict[str, Any]:
    dataset = Dataset.from_list(
        [
            {
                "inputs": {"question": "hello"},
                "reference_outputs": {"answer": "HELLO"},
            },
            {
                "inputs": {"question": "world"},
                "reference_outputs": {"answer": "WORLD"},
            },
        ]
    )
    result = await Experiment(
        base=Context(),
        variants={"upper": UpperAgent(), "lower": LowerAgent()},
        dataset=dataset,
        seed=_seed,
        evaluators=[_exact],
        name="tour",
    ).run()
    return {
        "winner": result.winner,
        "rows": result.table(),
        "table": result.render(),
        "dataset_version": result.dataset_version,
    }
