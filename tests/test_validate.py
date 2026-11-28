"""Static agent-contract validation (`validate_contracts`)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Step,
    ValidationError,
    validate_contracts,
)
from reactifact.consume import JoinConsume


class Question(BaseModel):
    text: str


class Middle(BaseModel):
    text: str


class Final(BaseModel):
    text: str


class ProducerStep(Step):
    reads = (Question,)
    produces = (Middle,)

    async def run(self, item: Question) -> Middle:
        return Middle(text=item.text)


class ConsumerStep(Step):
    reads = (Middle,)
    produces = (Final,)

    async def run(self, item: Middle) -> Final:
        return Final(text=item.text)


def _producer() -> Agent:
    return Agent("producer", steps=[ProducerStep()])


def _consumer() -> Agent:
    return Agent("consumer", steps=[ConsumerStep()])


def test_valid_contracts_are_clean():
    report = validate_contracts(
        agents=[_producer(), _consumer()],
        seeds=[Question],
        terminal=[Final],
    )

    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []
    assert report.render() == "reactifact validate: ok"


def test_unknown_input_is_an_error():
    report = validate_contracts(
        agents=[_consumer()], seeds=[Question], terminal=[Final]
    )

    assert report.ok is False
    assert [issue.code for issue in report.errors] == ["unknown_input"]
    assert "Middle" in report.errors[0].message
    with pytest.raises(ValidationError):
        report.raise_for_errors()


def test_dead_output_is_a_warning_unless_terminal():
    report = validate_contracts(agents=[_producer()], seeds=[Question], terminal=[])

    assert report.ok is True
    assert any(issue.code == "dead_output" for issue in report.warnings)

    clean = validate_contracts(
        agents=[_producer()], seeds=[Question], terminal=[Middle]
    )
    assert clean.warnings == []


def test_duplicate_agent_names_are_errors():
    report = validate_contracts(
        agents=[_producer(), _producer()],
        seeds=[Question],
        terminal=[Middle],
    )

    assert report.ok is False
    assert any(issue.code == "duplicate_agent" for issue in report.errors)


def test_cycle_without_budget_is_a_warning():
    class CycleStep(Step):
        reads = (Middle,)
        produces = (Question,)

        async def run(self, item: Middle) -> Question:
            return Question(text=item.text)

    cycle = Agent("cycle", steps=[CycleStep()])
    report = validate_contracts(
        agents=[_producer(), cycle],
        seeds=[Question],
        terminal=[Middle],
    )

    assert any(issue.code == "cycle" for issue in report.warnings)


def test_correlated_consume_requires_all_parts():
    class Join(Step):
        reads = (JoinConsume(Question, Middle, key=lambda item: item.text),)
        produces = (Final,)

        async def run(self, item: Question) -> Final:
            return Final(text=item.text)

    agent = Agent("join", steps=[Join()])
    report = validate_contracts(
        agents=[agent],
        seeds=[Question, Middle],
        terminal=[Final],
    )

    assert report.ok is True


def test_validate_contracts_accepts_raw_steps():
    report = validate_contracts(
        steps=[ProducerStep(), ConsumerStep()],
        seeds=[Question],
        terminal=[Final],
    )
    assert report.ok is True
    assert report.warnings == []


def test_validate_contracts_merges_steps_and_groups_in_order():
    report = validate_contracts(
        steps=[ProducerStep()],
        agents=[_consumer()],
        seeds=[Question],
        terminal=[Final],
    )
    assert report.ok is True


def test_validate_contracts_rejects_wrong_types():
    with pytest.raises(TypeError, match="steps= accepts Step"):
        validate_contracts(steps=[_producer()])
    with pytest.raises(TypeError, match="agents= accepts Agent"):
        validate_contracts(agents=[ProducerStep()])
