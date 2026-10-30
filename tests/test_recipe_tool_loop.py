"""recipes.run_tool_loop: bounded rounds, parallel calls, mandatory-tool retry."""

import asyncio
import time
from collections.abc import AsyncIterator

from reactifact import Budget, Context, RuntimeResources, ToolError, ToolOutput, tool
from reactifact.budget import BudgetTracker
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


def test_max_tool_calls_is_enforced():
    calls.clear()
    llm = ScriptedNativeLLM(
        [
            _tool_call("1", "search", '{"q": "a"}'),
            _tool_call("2", "search", '{"q": "b"}'),
            _answer("forced"),
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.resources.budget = Budget(max_tool_calls=1)

    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))

    assert calls == [("search", "a")]  # the second call never ran
    assert result.text == "forced"
    assert result.tools_used == ["search", "search"]
    assert result.observations[1].error.startswith("tool-call budget exhausted")


def test_time_budget_stops_before_a_round():
    calls.clear()
    llm = ScriptedNativeLLM([_answer("late answer")])
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.resources.budget = Budget(max_seconds=1)
    ctx.resources.budget_deadline = time.monotonic() - 1

    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))

    assert calls == []
    assert result.rounds == 0
    assert result.text == "late answer"


def test_token_budget_stops_before_a_round():
    calls.clear()
    llm = ScriptedNativeLLM([_answer("late answer")])
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.resources.budget = Budget(max_tokens=10)
    ctx.resources.budget_tracker = BudgetTracker()
    ctx.resources.budget_tracker.record(prompt_tokens=10, completion_tokens=0)

    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))

    assert calls == []
    assert result.rounds == 0
    assert result.text == "late answer"


@tool
async def long_output(q: str) -> str:
    """Return a long output."""
    return "x" * 100


@tool
async def leaky(q: str) -> str:
    """Return text containing PII."""
    return "contact bob@example.com"


def test_max_observation_chars_truncates_only_the_prompt():
    llm = ScriptedNativeLLM(
        [_tool_call("1", "long_output", '{"q": "a"}'), _answer("ok")]
    )
    result = asyncio.run(
        run_tool_loop(
            _context(llm),
            system="s",
            user="u",
            tools=[long_output],
            max_observation_chars=10,
        )
    )

    assert result.observations[0].output == "x" * 100  # caller keeps the full text
    tool_message = next(m for m in result.messages if m["role"] == "tool")
    assert tool_message["content"].startswith("x" * 10)
    assert "truncated 90 chars" in tool_message["content"]


def test_redact_tool_outputs_applies_the_resource_redactor():
    from reactifact.redaction import RegexRedactor

    ctx = _context(
        ScriptedNativeLLM([_tool_call("1", "leaky", '{"q": "a"}'), _answer("ok")])
    )
    ctx.resources.redactor = RegexRedactor()

    result = asyncio.run(
        run_tool_loop(
            ctx,
            system="s",
            user="u",
            tools=[leaky],
            redact_tool_outputs=True,
        )
    )

    assert result.observations[0].output == "contact [REDACTED:email]"
    tool_message = next(m for m in result.messages if m["role"] == "tool")
    assert "bob@example.com" not in tool_message["content"]


def test_on_event_reports_round_tool_and_final():
    events: list[tuple[str, dict]] = []
    llm = ScriptedNativeLLM([_tool_call("1", "search", '{"q": "x"}'), _answer("done")])

    asyncio.run(
        run_tool_loop(
            _context(llm),
            system="s",
            user="u",
            tools=[search],
            on_event=lambda name, payload: events.append((name, payload)),
        )
    )

    names = [name for name, _ in events]
    assert names.count("round_start") == 2
    assert "tool_call" in names
    assert "tool_result" in names
    assert names[-1] == "final"
    tool_call = next(payload for name, payload in events if name == "tool_call")
    assert tool_call["tool"] == "search"


def test_fallback_runs_for_textual_tool_markup():
    async def fallback(context, messages, text):
        assert "tool_calls" in text
        return "legacy answer"

    llm = ScriptedNativeLLM([_answer("<|tool_calls|>garbage")])
    result = asyncio.run(
        run_tool_loop(
            _context(llm), system="s", user="u", tools=[search], fallback=fallback
        )
    )

    assert result.text == "legacy answer"


def test_parse_calls_can_normalize_textual_tool_calls():
    from reactifact.native_tool_use import parse_tool_calls_with_textual

    calls.clear()
    llm = ScriptedNativeLLM(
        [
            _answer(
                '<tool_call>{"name": "search", "arguments": {"q": "x"}}</tool_call>'
            ),
            _answer("done"),
        ]
    )
    result = asyncio.run(
        run_tool_loop(
            _context(llm),
            system="s",
            user="u",
            tools=[search],
            parse_calls=parse_tool_calls_with_textual,
        )
    )

    assert calls == [("search", "x")]
    assert result.text == "done"


def test_parse_textual_tool_calls_reads_dsml():
    from reactifact.native_tool_use import parse_textual_tool_calls

    text = (
        '<｜tool_calls｜><｜invoke name="search">'
        '<｜parameter name="q" string="true">x</｜parameter>'
        "</｜invoke></｜tool_calls｜>"
    )
    calls = parse_textual_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "search"
    assert calls[0].args == {"q": "x"}


def test_stop_reason_completed():
    llm = ScriptedNativeLLM([_tool_call("1", "search", '{"q": "x"}'), _answer("done")])
    result = asyncio.run(
        run_tool_loop(_context(llm), system="s", user="u", tools=[search])
    )
    assert result.stop_reason == "completed"
    assert result.error is None


def test_stop_reason_max_rounds():
    llm = ScriptedNativeLLM(
        [_tool_call("1", "search", '{"q": "x"}'), _answer("forced")]
    )
    result = asyncio.run(
        run_tool_loop(_context(llm), system="s", user="u", tools=[search], max_rounds=1)
    )
    assert result.stop_reason == "max_rounds"
    assert result.text == "forced"


def test_stop_reason_budget():
    llm = ScriptedNativeLLM(
        [_tool_call("1", "search", '{"q": "x"}'), _answer("forced")]
    )
    ctx = _context(llm)
    ctx.resources.budget = Budget(max_tool_calls=0)

    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))

    assert result.stop_reason == "budget"
    assert result.observations[0].error


def test_stop_reason_provider_error():
    ctx = Context(resources=RuntimeResources())
    result = asyncio.run(run_tool_loop(ctx, system="s", user="u", tools=[search]))
    assert result.stop_reason == "provider_error"
    assert result.error is not None


def test_structured_tool_error_propagates():
    @tool
    async def broken(q: str) -> ToolOutput:
        """Always fails."""
        return ToolOutput.failed(
            ToolError(code="E_BROKEN", message="boom", retryable=True)
        )

    llm = ScriptedNativeLLM([_tool_call("1", "broken", '{"q": "x"}'), _answer("ok")])
    result = asyncio.run(
        run_tool_loop(_context(llm), system="s", user="u", tools=[broken])
    )

    observation = result.observations[0]
    assert observation.error == "boom"
    assert observation.error_info is not None
    assert observation.error_info.code == "E_BROKEN"
    assert observation.error_info.retryable is True


def test_retryable_tool_error_is_retried():
    attempts = {"n": 0}

    @tool
    async def flaky(q: str) -> ToolOutput:
        """Fail twice, then succeed."""
        attempts["n"] += 1
        if attempts["n"] < 3:
            return ToolOutput.failed(
                ToolError(code="E_TMP", message="try later", retryable=True)
            )
        return ToolOutput(text="ok")

    llm = ScriptedNativeLLM([_tool_call("1", "flaky", '{"q": "x"}'), _answer("done")])
    result = asyncio.run(
        run_tool_loop(
            _context(llm),
            system="s",
            user="u",
            tools=[flaky],
            retry_tool_errors=True,
            tool_retry_attempts=3,
            tool_retry_delay=0.0,
        )
    )

    assert attempts["n"] == 3
    assert result.observations[0].attempts == 3
    assert result.observations[0].output == "ok"


def test_non_retryable_tool_error_is_not_retried():
    attempts = {"n": 0}

    @tool
    async def broken(q: str) -> ToolOutput:
        """Always fails permanently."""
        attempts["n"] += 1
        return ToolOutput.failed(
            ToolError(code="E_BAD", message="nope", retryable=False)
        )

    llm = ScriptedNativeLLM([_tool_call("1", "broken", '{"q": "x"}'), _answer("done")])
    result = asyncio.run(
        run_tool_loop(
            _context(llm),
            system="s",
            user="u",
            tools=[broken],
            retry_tool_errors=True,
            tool_retry_attempts=3,
            tool_retry_delay=0.0,
        )
    )

    assert attempts["n"] == 1
    assert result.observations[0].attempts == 1


def test_retries_are_off_by_default():
    attempts = {"n": 0}

    @tool
    async def flaky(q: str) -> ToolOutput:
        """Fail once."""
        attempts["n"] += 1
        return ToolOutput.failed(
            ToolError(code="E_TMP", message="try later", retryable=True)
        )

    llm = ScriptedNativeLLM([_tool_call("1", "flaky", '{"q": "x"}'), _answer("done")])
    asyncio.run(run_tool_loop(_context(llm), system="s", user="u", tools=[flaky]))
    assert attempts["n"] == 1
