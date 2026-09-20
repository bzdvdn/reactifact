"""recipes.run_tool_loop: bounded rounds, parallel calls, mandatory-tool retry."""

import asyncio
from collections.abc import AsyncIterator

from reactifact import Context, RuntimeResources, tool
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
)
from reactifact.recipes import run_tool_loop


class ScriptedNativeLLM(LLMProvider):
    """Returns canned OpenAI bodies (with `tool_calls`) from `raw`."""

    def __init__(self, bodies: list[dict]):
        self.bodies = list(bodies)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = (
            self.bodies.pop(0)
            if self.bodies
            else {"choices": [{"message": {"content": "done"}}]}
        )
        message = (body.get("choices") or [{}])[0].get("message", {})
        return LLMResponse(text=message.get("content", "") or "", raw=body)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="")


def _tool_call(call_id: str, name: str, args: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                    ],
                }
            }
        ]
    }


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _context(llm: LLMProvider) -> Context:
    return Context(resources=RuntimeResources(llm=llm))


calls: list[tuple[str, str]] = []


@tool
async def search(q: str) -> str:
    """Search the knowledge base."""
    calls.append(("search", q))
    return f"result for {q}"


@tool
async def read(doc: str) -> str:
    """Read a document."""
    calls.append(("read", doc))
    return "doc content"


def test_tool_call_is_executed_then_answered():
    calls.clear()
    llm = ScriptedNativeLLM(
        [_tool_call("1", "search", '{"q": "x"}'), _answer("the answer")]
    )
    result = asyncio.run(
        run_tool_loop(_context(llm), system="s", user="u", tools=[search])
    )

    assert result.text == "the answer"
    assert result.tools_used == ["search"]
    assert calls == [("search", "x")]
    assert result.observations[0].output == "result for x"
    # transcript: system, user, assistant(tool_calls), tool
    assert [m["role"] for m in result.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_multiple_calls_in_one_turn_run_parallel():
    calls.clear()
    body = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {"name": "search", "arguments": '{"q": "a"}'},
                        },
                        {
                            "id": "2",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"doc": "b"}'},
                        },
                    ],
                }
            }
        ]
    }
    llm = ScriptedNativeLLM([body, _answer("ok")])
    result = asyncio.run(
        run_tool_loop(_context(llm), system="s", user="u", tools=[search, read])
    )

    assert result.text == "ok"
    assert sorted(result.tools_used) == ["read", "search"]
    assert sorted(calls) == [("read", "b"), ("search", "a")]


def test_mandatory_tool_is_nudged_until_called():
    calls.clear()
    llm = ScriptedNativeLLM(
        [
            _answer("premature answer"),  # no tool call yet
            _tool_call("1", "read", '{"doc": "d"}'),
            _answer("final"),
        ]
    )
    result = asyncio.run(
        run_tool_loop(
            _context(llm), system="s", user="u", tools=[read], mandatory="read"
        )
    )

    assert result.text == "final"
    assert calls == [("read", "d")]
    # the nudge was injected as a user reminder
    assert any("must call read" in m.get("content", "") for m in result.messages)


def test_no_provider_is_an_honest_empty_result():
    ctx = Context(resources=RuntimeResources())
    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))
    assert result.text == ""
    assert result.rounds == 0
    assert result.observations == []
