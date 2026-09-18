"""Replay (§55): record → replay LLM calls and deterministic state recovery."""

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.replay import ReplayLLM, ReplayMiss, replay_context, replay_summary
from reactifact.session import SessionStore
from reactifact.structured import structured_llm


class Word(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class Seed(BaseModel):
    text: str


class Pass(Produce[Note]):
    artifact_type = Note

    async def produce(self, call: ProduceCall):
        note1 = self.effects.create(Note(text="v1"), id="note:1")
        self.effects.create(Note(text="v2"), id="note:2")
        note1.link("supported_by", "note:2")
        return None


class PassAgent(Agent):
    name = "pass"
    consumes = [Consume(Seed)]
    produces = [Pass()]


async def _aran_session(tmp_path: str) -> tuple[SessionStore, str]:
    """Runs a real runtime pass and returns (store, session_id) with v1 == 1."""
    store = SessionStore(SQLiteKVBackend(tmp_path))
    ctx = Context(resources=RuntimeResources())
    ctx.create(Seed(text="go"))
    runtime = Runtime(ctx, agents=[PassAgent()], budget=Budget(max_runs=5))
    await runtime.arun()
    await store.save_session("demo", ctx)
    return store, "demo"


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=self.responses.pop(0), usage={"prompt_tokens": 1})

    async def stream(self, request):
        yield LLMResponse()  # pragma: no cover


def _llm_for(content: str) -> RuntimeResources:
    payload = '{"text": "' + content + '"}'
    return RuntimeResources(llm=ScriptedLLM([payload]))


def test_record_then_replay_reproduces_responses(tmp_path):
    recording = tmp_path / "calls.jsonl"
    recorder = ReplayLLM(
        recording, mode="record", inner=ScriptedLLM(['{"text": "one"}'])
    )
    ctx1 = Context(resources=RuntimeResources(llm=recorder))
    first = asyncio.run(structured_llm(ctx1, schema=Word, user="first"))
    assert first is not None and first.text == "one"
    assert recording.exists()
    assert len(recording.read_text(encoding="utf-8").splitlines()) == 1

    replay = ReplayLLM(recording, mode="replay")
    ctx2 = Context(resources=RuntimeResources(llm=replay))
    again = asyncio.run(structured_llm(ctx2, schema=Word, user="first"))
    assert again is not None and again.text == "one"


def test_replay_misses_raise_not_silently_wrong(tmp_path):
    from reactifact.providers import Message

    recording = tmp_path / "calls.jsonl"
    recorder = ReplayLLM(recording, mode="record", inner=ScriptedLLM(['{"text": "a"}']))
    ctx1 = Context(resources=RuntimeResources(llm=recorder))
    asyncio.run(structured_llm(ctx1, schema=Word, user="prompt-a"))

    replay = ReplayLLM(recording, mode="replay")
    ctx2 = Context(resources=RuntimeResources(llm=replay))
    good = asyncio.run(structured_llm(ctx2, schema=Word, user="prompt-a"))
    assert good is not None and good.text == "a"

    # provider level: an unrecorded call is a ReplayMiss, not a wrong answer
    tainted = LLMRequest(messages=[Message.user("won't exist")])
    try:
        asyncio.run(replay.complete(tainted))
    except ReplayMiss:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ReplayMiss for an unrecorded call")

    # structured layer: the miss degrades to an honest None (a fallback path)
    divergent = asyncio.run(structured_llm(ctx2, schema=Word, user="prompt-b"))
    assert divergent is None


def test_replay_records_model_and_usage(tmp_path):
    recording = tmp_path / "calls.jsonl"
    recorder = ReplayLLM(
        recording, mode="record", inner=ScriptedLLM(['{"text": "x"}']), model="toy"
    )
    ctx = Context(resources=RuntimeResources(llm=recorder))
    asyncio.run(structured_llm(ctx, schema=Word, user="hi"))
    entry = recording.read_text(encoding="utf-8").splitlines()[0]
    assert '"model": "toy"' in entry
    assert '"usage"' in entry
    assert '"text"' in entry


def test_replay_context_reconstructs_past_versions(tmp_path):
    asyncio.run(_test_replay_context_reconstructs_past_versions(tmp_path))


async def _test_replay_context_reconstructs_past_versions(tmp_path):
    store, session_id = await _aran_session(str(tmp_path / "sessions.sqlite3"))
    loaded = await replay_context(store, session_id)
    assert loaded.version == 1
    assert len(loaded.list_artifacts(Note)) == 2

    at_start = await replay_context(store, session_id, version=0)
    assert at_start.version == 0
    assert at_start.list_artifacts(Note) == []


def test_replay_summary_shape(tmp_path):
    asyncio.run(_test_replay_summary_shape(tmp_path))


async def _test_replay_summary_shape(tmp_path):
    store, session_id = await _aran_session(str(tmp_path / "sessions.sqlite3"))
    summary = replay_summary(await replay_context(store, session_id))
    assert summary["version"] == 1
    assert summary["artifacts"] == 3  # Seed input + 2 Notes
    assert summary["relations"] == 1
    assert summary["by_type"] == {"Note": 2, "Seed": 1}
