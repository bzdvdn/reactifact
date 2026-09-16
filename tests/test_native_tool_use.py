import asyncio
import json

from reactifact import Context, RuntimeResources, tool
from reactifact.native_tool_use import (
    ToolCall,
    native_complete,
    parse_tool_calls,
    tools_payload,
)
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse


@tool
async def check_pods(namespace: str) -> str:
    """Check pods."""
    return f"pods ok in {namespace}"


@tool(destructive=True)
async def restart_pod(name: str) -> str:
    """Restart a pod."""
    return f"restarted {name}"


class ScriptedRawLLM(LLMProvider):
    """Returns a fixed raw OpenAI-wire response body, and records the
    payload-relevant request fields it was called with."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.last_extra: dict | None = None
        self.last_messages: list | None = None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_extra = request.extra
        self.last_messages = request.messages
        message = self.raw["choices"][0]["message"]
        return LLMResponse(text=message.get("content") or "", raw=self.raw)

    async def stream(self, request):
        yield LLMResponse(text="")


def test_tools_payload_wraps_tool_schema_as_openai_function():
    payload = tools_payload([check_pods, restart_pod])
    assert payload[0]["type"] == "function"
    assert payload[0]["function"]["name"] == "check_pods"
    assert payload[0]["function"]["description"] == "Check pods."
    assert payload[0]["function"]["parameters"] == check_pods.schema
    assert payload[1]["function"]["name"] == "restart_pod"


def test_tools_payload_accepts_a_dict_too():
    payload = tools_payload({"check_pods": check_pods})
    assert len(payload) == 1
    assert payload[0]["function"]["name"] == "check_pods"


def test_parse_tool_calls_extracts_and_parses_arguments():
    response = LLMResponse(
        text="",
        raw={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "check_pods",
                                    "arguments": json.dumps({"namespace": "checkout"}),
                                },
                            }
                        ]
                    }
                }
            ]
        },
    )
    calls = parse_tool_calls(response)
    assert calls == [
        ToolCall(id="call_1", name="check_pods", args={"namespace": "checkout"})
    ]


def test_parse_tool_calls_empty_when_none_present():
    response = LLMResponse(text="hi", raw={"choices": [{"message": {"content": "hi"}}]})
    assert parse_tool_calls(response) == []


def test_parse_tool_calls_tolerates_malformed_arguments_json():
    response = LLMResponse(
        text="",
        raw={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "check_pods",
                                    "arguments": "{not json",
                                },
                            }
                        ]
                    }
                }
            ]
        },
    )
    calls = parse_tool_calls(response)
    assert calls == [ToolCall(id="call_1", name="check_pods", args={})]


def test_parse_tool_calls_handles_non_dict_raw():
    assert parse_tool_calls(LLMResponse(text="hi", raw=None)) == []
    assert parse_tool_calls(LLMResponse(text="hi", raw="not a dict")) == []


def test_native_complete_sends_messages_and_tools_via_extra():
    llm = ScriptedRawLLM({"choices": [{"message": {"content": "ok"}}]})
    ctx = Context(resources=RuntimeResources(llm=llm))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    response = asyncio.run(native_complete(ctx, messages=messages, tools=[check_pods]))

    assert response is not None
    assert response.text == "ok"
    assert llm.last_extra["messages"] == messages
    assert llm.last_extra["tools"][0]["function"]["name"] == "check_pods"


def test_native_complete_also_populates_typed_messages_as_a_fallback():
    llm = ScriptedRawLLM({"choices": [{"message": {"content": "ok"}}]})
    ctx = Context(resources=RuntimeResources(llm=llm))
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "check_pods", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "pods ok"},
    ]

    asyncio.run(native_complete(ctx, messages=messages))

    assert [m.role for m in llm.last_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert [m.content for m in llm.last_messages] == ["s", "u", "", "pods ok"]


def test_native_complete_omits_tools_when_none_given():
    llm = ScriptedRawLLM({"choices": [{"message": {"content": "ok"}}]})
    ctx = Context(resources=RuntimeResources(llm=llm))

    asyncio.run(native_complete(ctx, messages=[{"role": "user", "content": "hi"}]))

    assert "tools" not in llm.last_extra
    assert "tool_choice" not in llm.last_extra


def test_native_complete_returns_none_without_a_provider():
    ctx = Context(resources=RuntimeResources())
    response = asyncio.run(
        native_complete(ctx, messages=[{"role": "user", "content": "hi"}])
    )
    assert response is None


def test_native_complete_returns_none_on_provider_error():
    class FailingLLM(LLMProvider):
        async def complete(self, request):
            raise RuntimeError("boom")

        async def stream(self, request):
            yield LLMResponse(text="")

    ctx = Context(resources=RuntimeResources(llm=FailingLLM()))
    response = asyncio.run(
        native_complete(ctx, messages=[{"role": "user", "content": "hi"}])
    )
    assert response is None
