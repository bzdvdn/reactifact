"""`Route`: gate case groups by one artifact field and validate exclusivity."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Consume, Context, Runtime, Step, StepGroup
from reactifact.recipes import Route
from reactifact.validate import validate_contracts


class ResearchTurn(BaseModel):
    query_id: str
    domain: str


class Answer(BaseModel):
    text: str


class _Case(Step[Answer]):
    consumes = (Consume(ResearchTurn),)

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        super().__init__()

    async def run(self, turn: ResearchTurn, effects) -> None:
        effects.create(Answer(text=f"{self.prefix}:{turn.query_id}"))


def _group(prefix: str) -> StepGroup:
    return StepGroup(name=prefix, steps=[_Case(prefix)])


def test_route_runs_only_the_matching_case():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": _group("rag"), "meta": _group("meta")},
    )
    context = Context()
    context.create(ResearchTurn(query_id="q1", domain="rag"))
    context.create(ResearchTurn(query_id="q2", domain="meta"))
    asyncio.run(Runtime(context, groups=route.groups()).arun())
    answers = {a.data.text for a in context.list_artifacts(Answer)}
    assert answers == {"rag:q1", "meta:q2"}


def test_route_default_covers_unknown_values():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": _group("rag"), "meta": _group("meta")},
        default="rag",
    )
    context = Context()
    context.create(ResearchTurn(query_id="q", domain="other"))
    asyncio.run(Runtime(context, groups=route.groups()).arun())
    assert [a.data.text for a in context.list_artifacts(Answer)] == ["rag:q"]


def test_validate_warns_about_multiple_answer_producers():
    report = validate_contracts(groups=[_group("one"), _group("two")])
    assert any(issue.code == "ambiguous_producer" for issue in report.warnings)


def test_validate_suppresses_distinct_cases_of_one_route():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": _group("rag"), "meta": _group("meta")},
    )
    report = validate_contracts(groups=route.groups())
    assert not any(issue.code == "ambiguous_producer" for issue in report.warnings)


def test_route_requires_a_known_default():
    try:
        Route(
            on=ResearchTurn,
            key=lambda turn: turn.domain,
            cases={"rag": _group("rag")},
            default="meta",
        )
    except ValueError as exc:
        assert "default" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_same_case_with_two_producers_still_warns():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": [_group("a"), _group("b")]},
    )
    report = validate_contracts(groups=route.groups())
    assert any(issue.code == "ambiguous_producer" for issue in report.warnings)
