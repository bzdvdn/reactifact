"""The `reactifact.quick` on-ramp: facade behavior + graduation surface."""

import asyncio

from pydantic import BaseModel
from reactifact import Consume, Context, Effects, create_agent, produce
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.providers import FakeLLM, LLMProvider, LLMRequest, LLMResponse
from reactifact.quick import Answer, Question, agent, chat_agent, rag, tools_agent
from reactifact.recipes import keyword_score
from reactifact.resources import RuntimeResources
from reactifact.sources import FileSystemSource
from reactifact.tools import tool


class AnswerBody(BaseModel):
    text: str


class ScriptedLLM(LLMProvider):
    """Returns canned responses in order (last one repeats)."""

    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


# --- create_once_from ------------------------------------------------------- #


def test_create_once_from_derives_id_and_is_idempotent():
    ctx = Context()
    effects = Effects(ctx)

    handle = effects.create_once_from("q1", Answer(text="first"))
    assert handle is not None
    assert handle.id == "answer:q1"

    # Simulate the patch being applied, then retry the same source.
    ctx.create(Answer(text="first"), id="answer:q1")
    assert effects.create_once_from("q1", Answer(text="second")) is None


def test_create_once_from_custom_prefix_and_artifact_source():
    ctx = Context()
    effects = Effects(ctx)
    question = ctx.create(Question(text="hi"))

    handle = effects.create_once_from(question, Answer(text="a"), prefix="reply")
    assert handle is not None
    assert handle.id == f"reply:{question.id}"


# --- agent ------------------------------------------------------------------ #


def test_agent_without_provider_returns_none(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    qa = agent(system="Answer briefly.", schema=AnswerBody, llm=None)
    assert asyncio.run(qa.ask("anything")) is None
    assert qa.context is not None  # the run still happened
    assert qa.context.latest(AnswerBody) is None


def test_agent_returns_parsed_model_and_exposes_context():
    llm = ScriptedLLM(['{"text":"H2O"}'])
    qa = agent(system="Answer briefly.", schema=AnswerBody, llm=llm)

    body = asyncio.run(qa.ask("What is water?"))
    assert body is not None
    assert body.text == "H2O"
    # graduation surface: the real agent + the run's context
    assert qa.context is not None
    assert qa.context.latest(AnswerBody) is not None
    assert qa.agent.name == "quick"


# --- rag -------------------------------------------------------------------- #


def test_rag_answers_with_sources_and_provenance(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "refund.md").write_text(
        "Refunds are available within 14 days of purchase.", encoding="utf-8"
    )
    llm = ScriptedLLM(['{"text":"Within 14 days."}'])

    r = rag({"docs": docs}, llm=llm)
    answer = asyncio.run(r.ask("what is the refund policy?"))
    # `fold_plurals` lets "refund" match the document's "Refunds"

    assert answer is not None
    assert answer.text == "Within 14 days."
    assert answer.sources  # cited the materialized doc
    assert r.context is not None

    answer_art = r.context.latest(Answer)
    assert answer_art is not None
    # Answer —supported_by→ Doc —materialized_from→ SourceRef
    docs_used = r.context.related(answer_art.id, "supported_by")
    assert docs_used
    refs = r.context.related(docs_used[0].id, "materialized_from")
    assert refs


def test_rag_registers_plain_paths_with_matching_source_ids(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("alpha beta gamma", encoding="utf-8")

    r = rag({"docs": docs}, llm=FakeLLM())
    assert "docs" in r.resources.sources
    assert r.resources.sources["docs"].source_id == "docs"


def test_rag_forces_source_id_to_dict_key():
    source = FileSystemSource(root="/tmp", source_id="wrong", scorer=keyword_score)
    r = rag({"right": source}, llm=FakeLLM())
    assert source.source_id == "right"
    assert r.sources["right"].source_id == "right"


def test_rag_accepts_custom_doc_and_answer_models(tmp_path):
    """Bring your own artifact models instead of the generic Doc/Answer."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "refund.md").write_text(
        "Refunds are available within 14 days of purchase.", encoding="utf-8"
    )

    class MyDoc(BaseModel):
        body: str
        url: str

    class MyAnswer(BaseModel):
        answer: str
        citations: list[str] = []

    r = rag(
        {"docs": docs},
        doc_type=MyDoc,
        answer_type=MyAnswer,
        doc_factory=lambda _ctx, ref, content: MyDoc(
            body=content, url=ref.data.locator
        ),
        answer_factory=lambda text, used: MyAnswer(
            answer=text, citations=[d.data.url for d in used]
        ),
        doc_text=lambda d: d.body,
        doc_locator=lambda d: d.url,
        llm=ScriptedLLM(['{"text":"Within 14 days."}']),
    )
    answer = asyncio.run(r.ask("what is the refund policy?"))

    assert isinstance(answer, MyAnswer)
    assert answer.answer == "Within 14 days."
    assert answer.citations
    # provenance still uses the real artifact graph, regardless of model shape
    assert r.context is not None
    answer_art = r.context.latest(MyAnswer)
    assert answer_art is not None
    used = r.context.related(answer_art.id, "supported_by")
    assert used and isinstance(used[0].data, MyDoc)


def test_rag_custom_doc_type_without_factory_fails_loudly(tmp_path):
    import pytest

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "refund.md").write_text("Refunds within 14 days.", encoding="utf-8")

    class WeirdDoc(BaseModel):
        body: str

    r = rag({"docs": docs}, doc_type=WeirdDoc, llm=ScriptedLLM(['{"text":"x"}']))
    with pytest.raises(TypeError, match="doc_factory"):
        asyncio.run(r.ask("refund"))


def test_agent_accepts_custom_question_type():
    class MyQuestion(BaseModel):
        text: str
        lang: str = "en"

    qa = agent(
        system="Answer briefly.",
        schema=AnswerBody,
        question_type=MyQuestion,
        llm=ScriptedLLM(['{"text":"ok"}']),
    )
    body = asyncio.run(qa.ask("hello"))
    assert body is not None
    assert body.text == "ok"
    assert qa.context is not None
    assert qa.context.latest(MyQuestion) is not None


# --- tools_agent ------------------------------------------------------------ #

tool_calls: list[str] = []


@tool
async def echo_tool(value: str) -> str:
    """Echo a value back."""
    tool_calls.append(value)
    return f"echo:{value}"


def test_tools_agent_runs_the_tool_and_returns_answer():
    tool_calls.clear()
    llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"echo_tool","args":{"value":"x"}}',
            '{"type":"answer","text":"done"}',
        ]
    )
    t = tools_agent("Use echo_tool.", [echo_tool], llm=llm)

    text = asyncio.run(t.ask("please echo x"))
    assert text == "done"
    assert tool_calls == ["x"]


# --- chat_agent ------------------------------------------------------------- #


class ChatReply(BaseModel):
    text: str


@produce(ChatReply)
async def echo_reply(call):
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    call.effects.create(ChatReply(text=f"echo: {question.data.text}"))
    return None


def test_chat_agent_in_memory_session_and_history():
    agents = [create_agent("echo", consumes=[Consume(Question)], produces=[echo_reply])]
    assistant = chat_agent(agents, llm=FakeLLM())

    first = asyncio.run(assistant.invoke("hi", session_id="s1"))
    assert first["reply"] == "echo: hi"

    second = asyncio.run(assistant.invoke("again", session_id="s1"))
    assert second["reply"] == "echo: again"

    history = asyncio.run(assistant.history(session_id="s1"))
    texts = [m["text"] for m in history["messages"]]
    assert "hi" in texts and "echo: hi" in texts and "again" in texts


@produce(ChatReply)
async def llm_chat_reply(call):
    from reactifact.structured import llm_reply

    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    text = await llm_reply(call.context, system="Reply.", user=question.data.text)
    call.effects.create(ChatReply(text=text or "(none)"))
    return None


def test_chat_agent_passes_provider_to_the_turn():
    agents = [
        create_agent("llm", consumes=[Consume(Question)], produces=[llm_chat_reply])
    ]
    assistant = chat_agent(agents, llm=ScriptedLLM(['{"text":"from provider"}']))

    result = asyncio.run(assistant.invoke("hi", session_id="p"))
    assert result["reply"] == "from provider"


# --- InMemoryKVBackend ------------------------------------------------------ #


def test_in_memory_kv_backend_roundtrip_and_isolation():
    backend = InMemoryKVBackend()

    async def scenario():
        await backend.set("k", {"v": 1})
        stored = await backend.get("k")
        assert stored == {"v": 1}
        stored["v"] = 999  # mutating the returned copy must not alias
        assert await backend.get("k") == {"v": 1}
        assert await backend.keys() == ["k"]
        await backend.delete("k")
        assert await backend.get("k") is None

    asyncio.run(scenario())


# --- integration with plain Runtime ----------------------------------------- #


def test_quick_agent_composes_in_a_plain_runtime():
    """The graduation path: `.agent` is an ordinary reactive Agent."""
    from reactifact import Runtime

    llm = ScriptedLLM(['{"text":"ok"}'])
    qa = agent(system="Answer.", schema=AnswerBody, llm=llm)

    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Question(text="hello"))
    asyncio.run(Runtime(ctx, agents=[qa.agent]).arun())
    assert ctx.latest(AnswerBody) is not None
