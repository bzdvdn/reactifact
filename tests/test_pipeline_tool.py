"""`Pipeline.as_tool()` — schema and execution as a Tool (MCP-ready)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Pipeline, step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


def _pipeline() -> Pipeline:
    return Pipeline(steps=[answer], seed_type=Question, answer_type=Answer)


def test_as_tool_exposes_the_seed_schema_and_returns_the_answer():
    tool = _pipeline().as_tool(name="ask", description="Ask the pipeline")

    assert tool.name == "ask"
    assert tool.description == "Ask the pipeline"
    assert tool.schema["title"] == "Question"
    assert "text" in tool.schema["properties"]

    result = asyncio.run(tool.execute({"text": "hello"}))

    assert result.ok
    assert result.data == {"text": "HELLO"}
    assert result.text == "HELLO"


def test_as_tool_defaults_name_and_description():
    tool = _pipeline().as_tool()

    assert tool.name == "pipeline"
    assert "Question" in tool.description
    assert "Answer" in tool.description


def test_as_tool_requires_typed_models():
    pipeline = Pipeline(steps=[answer])

    with pytest.raises(TypeError):
        pipeline.as_tool()


def test_as_tool_mounts_in_an_mcp_server():
    pytest.importorskip("mcp")
    from reactifact.mcp import create_mcp_server

    server = create_mcp_server([_pipeline().as_tool(name="ask")])

    assert server is not None
