"""Redaction hook: trace text is sanitized, live state is not."""

import asyncio

from pydantic import BaseModel
from reactifact import Consume, Context, Runtime, RuntimeResources, produce
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.redaction import NoopRedactor, RegexRedactor
from reactifact.tracing import RunTrace, Tracer


class Question(BaseModel):
    text: str


class Contact(BaseModel):
    email: str


class ReplyBody(BaseModel):
    text: str


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


class CapturingTracer(Tracer):
    def __init__(self):
        super().__init__()
        self.trace: RunTrace | None = None

    async def on_turn_end(self, trace: RunTrace) -> None:
        self.trace = trace


def _run_with(redactor) -> tuple[Context, CapturingTracer]:
    from reactifact.structured import structured_llm

    @produce(Contact)
    async def make_contact(call):
        question = call.trigger
        if question is None or not isinstance(question.data, Question):
            return None
        body = await structured_llm(
            call.context,
            schema=ReplyBody,
            system="Reply.",
            user=f"contact: {question.data.text}",
        )
        call.effects.create(Contact(email="user@secret.com"))
        _ = body
        return None

    from reactifact import create_agent

    agent = create_agent(
        "contact", consumes=[Consume(Question)], produces=[make_contact]
    )
    resources = RuntimeResources(
        llm=ScriptedLLM(['{"text":"reply to reply@secret.com"}']),
        redactor=redactor,
    )
    ctx = Context(resources=resources)
    tracer = CapturingTracer()
    runtime = Runtime(ctx, agents=[agent], tracer=tracer)
    ctx.create(Question(text="ask a@secret.com"))
    asyncio.run(runtime.arun())
    return ctx, tracer


# --- built-in redactor ------------------------------------------------------ #


def test_regex_redactor_masks_known_patterns():
    redactor = RegexRedactor()
    text = (
        "email a@b.com ssn 123-45-6789 iban DE89370400440532013000 "
        "auth Bearer abc.def.ghi key sk-abcdefghijklmnopqrst"
    )
    out = redactor.redact(text)

    for secret in (
        "a@b.com",
        "123-45-6789",
        "DE89370400440532013000",
        "abc.def.ghi",
        "sk-abcdefghijklmnopqrst",
    ):
        assert secret not in out
    assert "[REDACTED:email]" in out
    assert "[REDACTED:us_ssn]" in out


def test_regex_redactor_leaves_plain_numbers_and_text():
    redactor = RegexRedactor()
    # a long digit run is not a card/phone by default — financial figures survive
    text = "total 1234567890123456 for 42 units of widget"
    assert redactor.redact(text) == text


def test_noop_redactor_changes_nothing():
    assert NoopRedactor().redact("a@b.com") == "a@b.com"


# --- end-to-end: traces sanitized, live state intact ------------------------ #


def test_redactor_sanitizes_trace_but_not_the_context():
    ctx, tracer = _run_with(RegexRedactor())

    assert tracer.trace is not None
    span = tracer.trace.spans[0]

    # LLM call text is redacted
    call = span.llm_calls[0]
    assert "reply@secret.com" not in call.response
    assert all("a@secret.com" not in (m.get("content") or "") for m in call.messages)

    # written artifact data is redacted
    contact_ref = next(r for r in span.writes if r.data_type == "Contact")
    assert contact_ref.data is not None
    assert "user@secret.com" not in contact_ref.data

    # ...but the live artifact (the resumable state) is untouched
    artifact = ctx.latest(Contact)
    assert artifact is not None
    assert artifact.data.email == "user@secret.com"


def test_without_redactor_trace_keeps_raw_data():
    ctx, tracer = _run_with(None)

    assert tracer.trace is not None
    span = tracer.trace.spans[0]
    call = span.llm_calls[0]
    assert "reply@secret.com" in call.response
    contact_ref = next(r for r in span.writes if r.data_type == "Contact")
    assert contact_ref.data is not None
    assert "user@secret.com" in contact_ref.data
