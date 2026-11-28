"""`Route`: gate case groups by one artifact field and validate exclusivity."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Runtime,
    Step,
)
from reactifact.recipes import Route
from reactifact.validate import validate_contracts


class ResearchTurn(BaseModel):
    query_id: str
    domain: str


class Answer(BaseModel):
    text: str


class UserQuery(BaseModel):
    text: str


class SearchDone(BaseModel):
    text: str


class _ProduceTurn(Step[ResearchTurn]):
    reads = (Consume(UserQuery),)

    async def run(self, query: UserQuery) -> ResearchTurn:
        return ResearchTurn(query_id="q1", domain="pipeline")


class _SearchStep(Step[SearchDone]):
    produces = (SearchDone,)

    async def run(self) -> SearchDone:
        return SearchDone(text="done")


class _ConsumeSearch(Step[Answer]):
    reads = (Consume(SearchDone),)

    async def run(self, done: SearchDone) -> Answer:
        return Answer(text=done.text)


class _FailStep(Step[Answer]):
    reads = (Consume(ResearchTurn),)

    async def run(self, turn: ResearchTurn) -> Answer:
        raise ValueError("boom")


class _FallbackAnswer(Step[Answer]):
    produces = (Answer,)

    async def run(self) -> Answer:
        return Answer(text="fallback")


class _Case(Step[Answer]):
    reads = (Consume(ResearchTurn),)

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        super().__init__()

    async def run(self, turn: ResearchTurn, effects) -> None:
        effects.create(Answer(text=f"{self.prefix}:{turn.query_id}"))


def _group(prefix: str) -> Agent:
    return Agent(name=prefix, steps=[_Case(prefix)])


def test_route_runs_only_the_matching_case():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": _group("rag"), "meta": _group("meta")},
    )
    context = Context()
    context.create(ResearchTurn(query_id="q1", domain="rag"))
    context.create(ResearchTurn(query_id="q2", domain="meta"))
    asyncio.run(Runtime(context, agents=route.agents()).arun())
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
    asyncio.run(Runtime(context, agents=route.agents()).arun())
    assert [a.data.text for a in context.list_artifacts(Answer)] == ["rag:q"]


def test_validate_warns_about_multiple_answer_producers():
    report = validate_contracts(agents=[_group("one"), _group("two")])
    assert any(issue.code == "ambiguous_producer" for issue in report.warnings)


def test_validate_suppresses_distinct_cases_of_one_route():
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"rag": _group("rag"), "meta": _group("meta")},
    )
    report = validate_contracts(agents=route.agents())
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
    report = validate_contracts(agents=route.agents())
    assert any(issue.code == "ambiguous_producer" for issue in report.warnings)


def test_routed_group_preserves_group_metadata():
    consume = Consume(
        ResearchTurn, condition=lambda artifact: artifact.data.domain == "pipeline"
    )
    fallback = _FallbackAnswer()
    inner = Agent(
        "inner",
        consumes=[consume],
        steps=[_SearchStep()],
        capabilities=("llm",),
        concurrency_limit=2,
        fallback=fallback,
        fallback_on=(ValueError,),
    )
    routed = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"pipeline": inner},
    ).agents()[0]

    assert routed.consumes == inner.consumes
    assert routed.consumes is not None
    assert routed.consumes[0].condition is consume.condition
    assert routed.capabilities == inner.capabilities
    assert routed.concurrency_limit == inner.concurrency_limit
    assert routed.fallback_step is fallback
    assert routed.fallback_on == (ValueError,)
    assert routed.fallback_skip == inner.fallback_skip

    context = Context()
    context.create(ResearchTurn(query_id="q1", domain="pipeline"))
    assert [a.data.query_id for a in routed.collect_inputs(context)] == ["q1"]


def test_validate_route_preserves_group_level_consumes():
    inner = Agent(
        "react",
        consumes=[Consume(ResearchTurn)],
        steps=[_SearchStep()],
    )
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"pipeline": inner},
    )
    groups = [
        *route.agents(),
        Agent("producer", consumes=[Consume(UserQuery)], steps=[_ProduceTurn()]),
        Agent("consumer", consumes=[Consume(SearchDone)], steps=[_ConsumeSearch()]),
    ]

    report = validate_contracts(
        agents=groups,
        seeds=[UserQuery],
        terminal=[Answer],
    )

    assert report.issues == []


def test_routed_group_keeps_fallback():
    inner = Agent(
        "inner",
        consumes=[Consume(ResearchTurn)],
        steps=[_FailStep()],
        fallback=_FallbackAnswer(),
    )
    route = Route(
        on=ResearchTurn,
        key=lambda turn: turn.domain,
        cases={"pipeline": inner},
    )

    context = Context()
    context.create(ResearchTurn(query_id="q1", domain="pipeline"))
    asyncio.run(Runtime(context, agents=route.agents()).arun())

    assert [a.data.text for a in context.list_artifacts(Answer)] == ["fallback"]
