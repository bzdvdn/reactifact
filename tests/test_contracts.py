"""Static contracts (`reactifact contracts`)."""

from __future__ import annotations

import json

from pydantic import BaseModel
from reactifact import RetryPolicy, Step, create_step_group, tool
from reactifact.cli import main
from reactifact.contracts import describe_contracts


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@tool
async def search(query: str) -> str:
    """Search the knowledge base."""
    return query


class Answerer(Step):
    consumes = (Question,)
    produces = (Answer,)
    tools = (search,)
    timeout = 5.0
    retry = RetryPolicy(max_attempts=2)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text)


GROUPS = [create_step_group("answer", steps=[Answerer()])]


def test_contract_report_has_steps_consumes_and_tools():
    report = describe_contracts(GROUPS)
    group = report.groups[0]
    assert group.name == "answer"
    assert group.steps[0].consumes[0].artifact == "Question"
    assert group.steps[0].produces == ("Answer",)
    assert group.steps[0].tools[0].name == "search"
    assert group.steps[0].tools[0].description.startswith("Search")
    assert group.steps[0].timeout == 5.0
    assert group.steps[0].retry == {
        "max_attempts": 2,
        "backoff": 0.5,
        "max_backoff": None,
    }


def test_contract_markdown_and_json_render():
    report = describe_contracts(GROUPS)
    markdown = report.to_markdown()
    assert "reactifact contracts" in markdown
    assert "`Question`" in markdown
    assert "`Answer`" in markdown
    assert "`search`" in markdown
    assert "timeout: 5s" in markdown

    payload = json.loads(report.to_json())
    assert payload["groups"][0]["steps"][0]["produces"] == ["Answer"]
    assert payload["groups"][0]["produces"] == ["Answer"]

    graph = report.to_mermaid()
    assert graph.startswith("flowchart LR")
    assert 'G0["answer"]' in graph
    assert 'A0["Question"]' in graph
    assert "consumes" in graph and "produces" in graph
    assert "TRUNC" in report.to_mermaid(max_nodes=1, max_edges=0)


def test_contracts_cli(capsys):
    assert main(["contracts", "tests.test_contracts:GROUPS"]) == 0
    out = capsys.readouterr().out
    assert "## `answer`" in out
    assert "`search`" in out
