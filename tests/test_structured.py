import asyncio
import json

from pydantic import BaseModel
from reactifact import Consume, Context, Runtime, RuntimeResources
from reactifact.llm_agent import StructuredGenerateAgent
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.structured import (
    chat_complete,
    chat_complete_full,
    json_schema_llm,
    parse_structured,
    structured_llm,
)


class Summary(BaseModel):
    text: str
    topics: list[str] = []


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else ""
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")  # pragma: no cover


def test_tolerant_parse_with_fences_and_noise():
    raw = 'Вот ответ:\n```json\n{"text": "итог", "topics": ["a", "b"]}\n```\nспасибо'
    summary = parse_structured(raw, Summary)
    assert summary is not None
    assert summary.text == "итог"
    assert summary.topics == ["a", "b"]


def test_parse_ignores_garbage():
    assert parse_structured("не json вообще", Summary) is None


def test_structured_llm_retries_on_invalid_json():
    llm = ScriptedLLM(
        [
            "К сожалению, не смог сгенерировать.",
            '{"text": "ок", "topics": []}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=llm))

    result = asyncio.run(structured_llm(ctx, schema=Summary, user="сделай итог"))
    assert result is not None
    assert result.text == "ок"
    assert len(llm.responses) == 0  # both calls used


def test_structured_llm_returns_none_on_all_failures():
    llm = ScriptedLLM(["мусор", "ещё мусор"])
    ctx = Context(resources=RuntimeResources(llm=llm))

    result = asyncio.run(structured_llm(ctx, schema=Summary, user="итог", attempts=2))
    assert result is None


class Report(BaseModel):
    title: str
    body: str


class Article(BaseModel):
    content: str


class ReportGenerator(StructuredGenerateAgent):
    name = "report_generator"
    schema = Report
    consumes = [Consume(Article)]

    def build_prompt(self, inputs):
        return f"Сделай отчёт о статье: {inputs[0].data.content}"

    def fallback(self, inputs):
        return Report(title="fallback", body=inputs[0].data.content[:50])


def test_structured_generate_agent_uses_llm():
    llm = ScriptedLLM(['{"title": "Т", "body": "Б"}'])
    ctx = Context(resources=RuntimeResources(llm=llm))
    runtime = Runtime(ctx, agents=[ReportGenerator()])
    ctx.create(Article(content="статья про отчёты"))
    asyncio.run(runtime.arun())

    reports = ctx.list_artifacts(Report)
    assert len(reports) == 1
    assert reports[0].data.title == "Т"
    commit = ctx.history()[-1]
    assert commit.author == "report_generator"


def test_structured_generate_fallback_on_llm_failure():
    llm = ScriptedLLM(["не валидный json", "тоже не валидный"])
    ctx = Context(resources=RuntimeResources(llm=llm))
    runtime = Runtime(ctx, agents=[ReportGenerator()])
    ctx.create(Article(content="очень длинная статья"))
    asyncio.run(runtime.arun())

    reports = ctx.list_artifacts(Report)
    assert len(reports) == 1
    assert reports[0].data.title == "fallback"


def test_structured_llm_without_llm_returns_none():
    ctx = Context()
    result = asyncio.run(structured_llm(ctx, schema=Summary, user="x"))
    assert result is None


def test_llm_reply_returns_plain_text():
    from reactifact.structured import llm_reply

    llm = ScriptedLLM(['{"text": "просто ответ"}'])
    ctx = Context(resources=RuntimeResources(llm=llm))
    text = asyncio.run(llm_reply(ctx, system="Будь краток", user="сколько будет 2+2?"))
    assert text == "просто ответ"


def test_llm_reply_is_none_on_honest_failure():
    from reactifact.structured import llm_reply

    llm = ScriptedLLM(["не json", "тоже не json"])
    ctx = Context(resources=RuntimeResources(llm=llm))
    text = asyncio.run(llm_reply(ctx, user="х"))
    assert text is None


def test_llm_reply_without_model_returns_none():
    from reactifact.structured import llm_reply

    ctx = Context()
    assert asyncio.run(llm_reply(ctx, user="х")) is None


class FlakyLLM(LLMProvider):
    def __init__(self):
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("network blip")
        return LLMResponse(text='{"text": "ok", "topics": []}')

    async def stream(self, request):
        yield LLMResponse(text="")


def test_structured_llm_retries_on_network_error():
    llm = FlakyLLM()
    ctx = Context(resources=RuntimeResources(llm=llm))
    result = asyncio.run(structured_llm(ctx, schema=Summary, user="x"))
    assert result is not None
    assert result.text == "ok"
    assert llm.calls == 2


class AlwaysFailsLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("provider is down")

    async def stream(self, request):
        yield LLMResponse(text="")  # pragma: no cover


def test_on_error_distinguishes_no_provider_from_provider_failure():
    reasons: list[tuple[str, Exception | None]] = []

    def record(reason, exc):
        reasons.append((reason, exc))

    ctx_no_provider = Context()
    result = asyncio.run(
        structured_llm(
            ctx_no_provider, schema=Summary, user="x", on_error=record, attempts=1
        )
    )
    assert result is None
    assert reasons == [("no_provider", None)]

    reasons.clear()
    ctx_down = Context(resources=RuntimeResources(llm=AlwaysFailsLLM()))
    result = asyncio.run(
        structured_llm(ctx_down, schema=Summary, user="x", on_error=record, attempts=1)
    )
    assert result is None
    assert len(reasons) == 1
    assert reasons[0][0] == "provider_error"
    assert isinstance(reasons[0][1], RuntimeError)


def test_on_error_reports_parse_error():
    reasons: list[tuple[str, Exception | None]] = []
    llm = ScriptedLLM(["мусор", "ещё мусор"])
    ctx = Context(resources=RuntimeResources(llm=llm))
    result = asyncio.run(
        structured_llm(
            ctx,
            schema=Summary,
            user="итог",
            attempts=2,
            on_error=lambda reason, exc: reasons.append((reason, exc)),
        )
    )
    assert result is None
    assert reasons == [("parse_error", None)]


def test_prompt_binds_system_and_schema():
    from reactifact.structured import StructuredLLM

    seen = {}

    class CapturingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            seen["system"] = request.messages[0].content
            seen["user"] = request.messages[-1].content
            return LLMResponse(text='{"text": "роль", "topics": []}')

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    llm = CapturingLLM()
    ctx = Context(resources=RuntimeResources(llm=llm))
    role = StructuredLLM(schema=Summary, system="You are a strict analyst.")
    result = asyncio.run(role.call(ctx, user="сделай итог по тексту"))
    assert result is not None
    assert result.text == "роль"
    assert seen["system"] == "You are a strict analyst."
    assert "сделай итог по тексту" in seen["user"]


RAW_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def test_json_schema_llm_parses_raw_dict_no_model_needed():
    llm = ScriptedLLM(['{"answer": "42"}'])
    ctx = Context(resources=RuntimeResources(llm=llm))

    result = asyncio.run(
        json_schema_llm(ctx, json_schema=RAW_SCHEMA, user="what is the answer?")
    )
    assert result == {"answer": "42"}


def test_json_schema_llm_accepts_a_json_string_too():
    llm = ScriptedLLM(['{"answer": "ok"}'])
    ctx = Context(resources=RuntimeResources(llm=llm))

    result = asyncio.run(
        json_schema_llm(ctx, json_schema=json.dumps(RAW_SCHEMA), user="q")
    )
    assert result == {"answer": "ok"}


def test_json_schema_llm_sends_strict_native_response_format():
    seen = {}

    class CapturingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            seen["response_format"] = request.response_format
            return LLMResponse(text='{"answer": "x"}')

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    ctx = Context(resources=RuntimeResources(llm=CapturingLLM()))
    asyncio.run(
        json_schema_llm(ctx, json_schema=RAW_SCHEMA, schema_name="my_schema", user="q")
    )
    assert seen["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "my_schema", "schema": RAW_SCHEMA, "strict": True},
    }


def test_json_schema_llm_retries_and_gives_up_honestly():
    llm = ScriptedLLM(["not json", "still not json"])
    ctx = Context(resources=RuntimeResources(llm=llm))

    result = asyncio.run(
        json_schema_llm(ctx, json_schema=RAW_SCHEMA, user="q", attempts=2)
    )
    assert result is None


def test_json_schema_llm_returns_none_without_a_provider():
    ctx = Context(resources=RuntimeResources())
    result = asyncio.run(json_schema_llm(ctx, json_schema=RAW_SCHEMA, user="q"))
    assert result is None


def test_chat_complete_sends_messages_as_is_and_returns_raw_text():
    seen = {}

    class CapturingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            seen["messages"] = [(m.role, m.content) for m in request.messages]
            return LLMResponse(text="raw reply, no envelope")

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    ctx = Context(resources=RuntimeResources(llm=CapturingLLM()))
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    result = asyncio.run(chat_complete(ctx, messages))

    assert result == "raw reply, no envelope"
    assert seen["messages"] == [("system", "s"), ("user", "u"), ("assistant", "a")]


def test_chat_complete_accepts_message_objects_too():
    from reactifact.providers import Message

    llm = ScriptedLLM(["ok"])
    ctx = Context(resources=RuntimeResources(llm=llm))
    result = asyncio.run(chat_complete(ctx, [Message.user("hi")]))
    assert result == "ok"


def test_chat_complete_returns_none_without_a_provider():
    ctx = Context(resources=RuntimeResources())
    result = asyncio.run(chat_complete(ctx, [{"role": "user", "content": "hi"}]))
    assert result is None


def test_chat_complete_returns_none_on_provider_error():
    class FailingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("boom")

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    ctx = Context(resources=RuntimeResources(llm=FailingLLM()))
    result = asyncio.run(chat_complete(ctx, [{"role": "user", "content": "hi"}]))
    assert result is None


def test_chat_complete_full_exposes_finish_reason_that_chat_complete_discards():
    """`chat_complete` collapses to `.text` alone — a caller that needs to
    tell a token-cap truncation apart from every other reason a reply ended
    (to retry only that case) has to reach for `chat_complete_full` instead."""

    class TruncatingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text="cut off mid-sen", finish_reason="length")

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    ctx = Context(resources=RuntimeResources(llm=TruncatingLLM()))
    response = asyncio.run(chat_complete_full(ctx, [{"role": "user", "content": "hi"}]))

    assert response is not None
    assert response.text == "cut off mid-sen"
    assert response.finish_reason == "length"


def test_chat_complete_full_returns_none_without_a_provider():
    ctx = Context(resources=RuntimeResources())
    result = asyncio.run(chat_complete_full(ctx, [{"role": "user", "content": "hi"}]))
    assert result is None


def test_chat_complete_full_returns_none_on_provider_error():
    class FailingLLM(LLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("boom")

        async def stream(self, request):
            yield LLMResponse(text="")  # pragma: no cover

    ctx = Context(resources=RuntimeResources(llm=FailingLLM()))
    result = asyncio.run(chat_complete_full(ctx, [{"role": "user", "content": "hi"}]))
    assert result is None
