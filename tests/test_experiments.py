"""Experiment/Arena: variants on a dataset, scored, with a winner."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Step
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


class UpperAgent(Agent):
    consumes = [Consume(Question)]

    def __init__(self) -> None:
        super().__init__(name="upper", steps=[UpperStep()])


class LowerAgent(Agent):
    consumes = [Consume(Question)]

    def __init__(self) -> None:
        super().__init__(name="lower", steps=[LowerStep()])


def seed(context: Context, inputs) -> None:
    context.create(Question(text=inputs["question"]))


def exact(eval_input) -> float:
    expected = (eval_input.example.reference_outputs or {}).get("answer")
    return 1.0 if eval_input.outputs.get("answer") == expected else 0.0


def test_experiment_picks_the_higher_quality_variant():
    dataset = Dataset.from_list(
        [
            {"inputs": {"question": "hello"}, "reference_outputs": {"answer": "HELLO"}},
            {"inputs": {"question": "world"}, "reference_outputs": {"answer": "WORLD"}},
        ]
    )
    base = Context()
    seen: list[str] = []

    experiment = Experiment(
        base=base,
        variants={
            "upper": [UpperAgent()],
            "lower": [LowerAgent()],
        },
        dataset=dataset,
        seed=seed,
        evaluators=[exact],
        on_result=lambda variant, _result: seen.append(variant),
    )
    result = asyncio.run(experiment.run())

    assert result.winner == "upper"
    assert result.dataset_version == dataset.version
    assert [variant.name for variant in result.variants] == ["upper", "lower"]
    upper, lower = result.variants
    assert upper.quality() == 1.0
    assert lower.quality() == 0.0
    assert len(upper.contexts) == 2
    assert len(upper.stats) == 2
    assert upper.contexts[0].latest(Answer) is not None
    assert base.latest(Answer) is None  # branches do not mutate the base
    assert set(seen) == {"upper", "lower"}
    assert "*" in result.render()


def test_experiment_metric_selects_the_ranking_key():
    def style(eval_input) -> dict[str, float]:
        text = str(eval_input.outputs.get("answer", ""))
        return {
            "exact": 1.0 if text.isupper() else 0.0,
            "style": 1.0 if text.islower() else 0.0,
        }

    dataset = Dataset.from_list(
        [{"inputs": {"question": "hello"}, "reference_outputs": {"answer": "HELLO"}}]
    )
    experiment = Experiment(
        base=Context(),
        variants={"upper": [UpperAgent()], "lower": [LowerAgent()]},
        dataset=dataset,
        seed=seed,
        evaluators=[style],
        metric="style",
    )

    result = asyncio.run(experiment.run())

    assert result.winner == "lower"
    assert result.metric == "style"
    assert result.variants[0].quality("exact") == 1.0
    assert result.variants[0].quality("style") == 0.0


def test_experiment_supports_async_seed_and_single_agent_variant():
    async def async_seed(context: Context, inputs) -> None:
        context.create(Question(text=inputs["question"]))

    dataset = Dataset.from_list(
        [{"inputs": {"question": "hello"}, "reference_outputs": {"answer": "HELLO"}}]
    )
    experiment = Experiment(
        base=Context(),
        variants={"upper": UpperAgent()},
        dataset=dataset,
        seed=async_seed,
        evaluators=[exact],
    )

    result = asyncio.run(experiment.run())

    assert result.winner == "upper"
    assert len(result.variants[0].contexts) == 1
