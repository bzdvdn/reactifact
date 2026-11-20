"""Checkpoints and forks over the commit history."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, Consume, Patch, Runtime, SessionStore
from reactifact.checkpoints import FileKVBackend
from reactifact.commit import ContextCheckpoint
from reactifact.replay import replay_context


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Agent):
    consumes = [Consume(Question)]

    async def run(self, event, context):
        question = context.get(event.artifact_id)
        if question is None:
            return None
        return Patch().create(Answer(text=question.data.text.upper()))


async def _seeded(tmp_path, session_id: str = "s1"):
    store = SessionStore(FileKVBackend(str(tmp_path / "sessions")))
    session = await store.open(session_id)
    runtime = Runtime(session.context, agents=[Answerer()], session=session)
    session.context.create(Question(text="hello"))
    await runtime.arun()
    return session, runtime, store


def test_checkpoints_project_the_commit_history(tmp_path):
    async def go():
        session, _runtime, store = await _seeded(tmp_path)
        context = session.context

        checkpoints = context.checkpoints()
        assert len(checkpoints) == 1
        checkpoint = checkpoints[0]
        assert isinstance(checkpoint, ContextCheckpoint)
        assert checkpoint.version == 1
        assert checkpoint.author == "Answerer"
        assert checkpoint.message
        assert [write.artifact_id for write in checkpoint.writes]
        assert checkpoint.parent_ids == ()

        assert context.checkpoint(checkpoint.id) == checkpoint
        assert context.checkpoint(1) == checkpoint
        assert context.checkpoint("missing") is None

        same = await store.checkpoints("s1")
        assert same == checkpoints

    asyncio.run(go())


def test_at_accepts_a_checkpoint_without_rewinding(tmp_path):
    async def go():
        session, runtime, _store = await _seeded(tmp_path)
        context = session.context
        checkpoint = context.checkpoints()[0]

        snapshot = context.at(checkpoint)
        assert snapshot is not context
        assert snapshot.version == checkpoint.version
        assert snapshot.resources is context.resources
        assert snapshot.list_artifacts(Answer)[0].data.text == "HELLO"

        # The live context is untouched and can keep committing.
        assert context.version == 1
        context.create(Question(text="again"))
        await runtime.arun()
        assert context.version == 2
        assert snapshot.version == 1
        assert len(snapshot.list_artifacts(Answer)) == 1

        with pytest.raises(KeyError):
            context.at("missing")

    asyncio.run(go())


def test_fork_starts_an_independent_session_at_a_checkpoint(tmp_path):
    async def go():
        session, runtime, store = await _seeded(tmp_path)
        checkpoint = session.checkpoints()[0]

        fork = await store.fork("s1", new_id="s1-try2", at=checkpoint.id)
        assert fork.loaded is True
        assert fork.session_id == "s1-try2"
        assert fork.context.version == checkpoint.version
        assert fork.context.list_artifacts(Answer)[0].data.text == "HELLO"

        # A change in the fork does not leak back.
        fork.context.create(Answer(text="only-in-fork"), id="fork-answer")
        await fork.save()
        source = await store.load_session("s1")
        assert source is not None
        assert not any(
            artifact.id == "fork-answer" for artifact in source.list_artifacts(Answer)
        )
        forked = await store.load_session("s1-try2")
        assert forked is not None
        assert any(
            artifact.id == "fork-answer" for artifact in forked.list_artifacts(Answer)
        )

        # The source keeps working normally.
        session.context.create(Question(text="after-fork"))
        await runtime.arun()
        assert session.context.version == 2

    asyncio.run(go())


def test_fork_can_start_from_head_or_a_version(tmp_path):
    async def go():
        session, _runtime, store = await _seeded(tmp_path)

        head = await session.fork("s1-head")
        assert head.context.version == session.context.version

        by_version = await store.fork("s1", new_id="s1-v1", at=1)
        assert by_version.context.version == 1

        with pytest.raises(ValueError, match="already exists"):
            await store.fork("s1", new_id="s1-v1")

        with pytest.raises(KeyError, match="missing"):
            await store.fork("missing", new_id="x")

    asyncio.run(go())


def test_replay_context_accepts_a_checkpoint(tmp_path):
    async def go():
        session, _runtime, store = await _seeded(tmp_path)
        checkpoint = session.checkpoints()[0]

        by_id = await replay_context(store, "s1", at=checkpoint.id)
        assert by_id.version == checkpoint.version
        assert by_id.list_artifacts(Answer)[0].data.text == "HELLO"

        by_object = await replay_context(store, "s1", at=checkpoint)
        assert by_object.version == checkpoint.version

        by_version = await replay_context(store, "s1", version=1)
        assert by_version.version == 1

        with pytest.raises(ValueError, match="either"):
            await replay_context(store, "s1", at=checkpoint, version=1)

    asyncio.run(go())
