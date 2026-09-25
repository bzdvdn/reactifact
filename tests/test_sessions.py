import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime, SessionStore
from reactifact.checkpoints import FileKVBackend, SQLiteKVBackend


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class SimpleAnswerer(Agent):
    consumes = [Consume(Question)]
    produces = []

    async def run(self, event, context):
        q = context.get(event.artifact_id)
        if q is None:
            return None
        return Patch().create(Answer(text=q.data.text.upper()))


async def make_session(tmp_path, session_id="alice", backend_kind="file"):
    from reactifact.resources import RuntimeResources

    if backend_kind == "file":
        backend = FileKVBackend(str(tmp_path / "sessions"))
    else:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
    store = SessionStore(backend)
    session = await store.open(session_id, resources=RuntimeResources())
    runtime = Runtime(session.context, agents=[SimpleAnswerer()], session=session)
    return session, runtime, store


def test_session_save_load_roundtrip(tmp_path):
    asyncio.run(_test_session_save_load_roundtrip(tmp_path))


async def _test_session_save_load_roundtrip(tmp_path):
    session, runtime, store = await make_session(tmp_path)

    assert session.loaded is False
    session.context.create(Question(text="казах"))
    await runtime.arun()

    assert session.context.version == 1
    assert session.context.head_id is not None

    # load again, like on a process restart
    session2 = await store.open("alice")
    assert session2.loaded is True
    assert session2.context.version == 1
    assert session2.context.head_id == session.context.head_id
    answers = session2.context.list_artifacts(Answer)
    assert len(answers) == 1
    assert answers[0].data.text == "КАЗАХ"


def test_session_auto_persist_after_commit(tmp_path):
    asyncio.run(_test_session_auto_persist_after_commit(tmp_path))


async def _test_session_auto_persist_after_commit(tmp_path):
    session, runtime, store = await make_session(tmp_path)

    session.context.create(Question(text="hi"))
    await runtime.arun_once()

    # data is saved automatically after the commit
    restored = await store.load_session("alice")
    assert restored is not None
    assert len(restored.list_artifacts(Answer)) == 1


def test_sessions_are_isolated(tmp_path):
    asyncio.run(_test_sessions_are_isolated(tmp_path))


async def _test_sessions_are_isolated(tmp_path):
    session_a, runtime_a, store = await make_session(tmp_path, "alice")
    session_b, runtime_b, store = await make_session(tmp_path, "bob")

    session_a.context.create(Question(text="a"))
    await runtime_a.arun()

    session_b.context.create(Question(text="b"))
    await runtime_b.arun()

    loaded_a = await store.load_session("alice")
    loaded_b = await store.load_session("bob")
    assert len(loaded_a.list_artifacts(Answer)) == 1
    assert len(loaded_b.list_artifacts(Answer)) == 1
    assert loaded_a.list_artifacts(Answer)[0].data.text == "A"
    assert loaded_b.list_artifacts(Answer)[0].data.text == "B"


def test_sqlite_kv_roundtrip(tmp_path):
    asyncio.run(_test_sqlite_kv_roundtrip(tmp_path))


async def _test_sqlite_kv_roundtrip(tmp_path):
    session, runtime, store = await make_session(
        tmp_path, "carol", backend_kind="sqlite"
    )

    session.context.create(Question(text="sqlite"))
    await runtime.arun()

    assert set(await store.list_sessions()) == {"carol"}
    restored = await store.load_session("carol")
    assert restored.list_artifacts(Answer)[0].data.text == "SQLITE"


def test_followup_reuses_context(tmp_path):
    asyncio.run(_test_followup_reuses_context(tmp_path))


async def _test_followup_reuses_context(tmp_path):
    """Continuing a conversation in the same session does not restart from scratch."""

    session, runtime, store = await make_session(tmp_path)

    session.context.create(Question(text="first"))
    await runtime.arun()
    first_version = session.context.version

    session.context.create(Question(text="second"))
    await runtime.arun()
    second_version = session.context.version

    assert second_version > first_version
    assert len(session.context.list_artifacts(Answer)) == 2
    assert len(session.context.history()) == second_version


def test_delete_session(tmp_path):
    asyncio.run(_test_delete_session(tmp_path))


async def _test_delete_session(tmp_path):
    session, runtime, store = await make_session(tmp_path)
    session.context.create(Question(text="x"))
    await runtime.arun()

    assert await store.has_session("alice")
    await session.delete()
    assert not await store.has_session("alice")
    assert await store.list_sessions() == []


class Echoed(BaseModel):
    text: str


class Echo(Agent):
    """Reacts to `Answer` — chains a second commit within the same `arun()`
    turn, so per_commit vs. per_turn save counts actually differ."""

    consumes = [Consume(Answer)]
    produces = []

    async def run(self, event, context):
        a = context.get(event.artifact_id)
        if a is None:
            return None
        return Patch().create(Echoed(text=a.data.text))


async def _make_counting_session(tmp_path, session_id, backend_name):
    from reactifact.resources import RuntimeResources

    backend = SQLiteKVBackend(str(tmp_path / f"{backend_name}.db"))
    store = SessionStore(backend)
    session = await store.open(session_id, resources=RuntimeResources())
    calls = 0
    original_save = session.save

    async def counting_save():
        nonlocal calls
        calls += 1
        await original_save()

    session.save = counting_save
    return session, store, (lambda: calls)


def test_session_save_policy_per_commit_is_the_default(tmp_path):
    asyncio.run(_test_session_save_policy_per_commit_is_the_default(tmp_path))


async def _test_session_save_policy_per_commit_is_the_default(tmp_path):
    session, _store, calls = await _make_counting_session(
        tmp_path, "erin", "per_commit"
    )
    runtime = Runtime(
        session.context, agents=[SimpleAnswerer(), Echo()], session=session
    )

    session.context.create(Question(text="hi"))
    await runtime.arun()

    # One save per generation boundary, after its trigger batch is consumed:
    # Answer's commit, then Echoed's, then the settling generation that
    # consumes Echoed's event with nothing left to react to. Persisting the
    # settle (not just the commits) is what keeps the saved queue consistent,
    # so a restart does not replay an already-consumed trigger.
    assert calls() == 3


def test_session_save_policy_per_turn_saves_once_for_a_multi_commit_turn(tmp_path):
    asyncio.run(
        _test_session_save_policy_per_turn_saves_once_for_a_multi_commit_turn(tmp_path)
    )


async def _test_session_save_policy_per_turn_saves_once_for_a_multi_commit_turn(
    tmp_path,
):
    session, store, calls = await _make_counting_session(tmp_path, "dave", "per_turn")
    runtime = Runtime(
        session.context,
        agents=[SimpleAnswerer(), Echo()],
        session=session,
        session_save_policy="per_turn",
    )

    session.context.create(Question(text="hi"))
    await runtime.arun()

    assert len(session.context.list_artifacts(Answer)) == 1
    assert len(session.context.list_artifacts(Echoed)) == 1
    assert calls() == 1  # one save for the whole multi-commit turn, not one per commit

    # still durably persisted — deferring is not skipping.
    restored = await store.load_session("dave")
    assert restored.list_artifacts(Echoed)[0].data.text == "HI"


def test_context_dict_roundtrip_keeps_pending_events():
    ctx = Context()
    ctx.create(Question(text="q"))

    restored = Context.from_dict(ctx.to_dict())

    pending = restored.pending_events()
    assert len(pending) == 1
    assert pending[0].artifact_type is Question
    assert pending[0].type.value == "artifact_created"


def test_session_restores_pending_events_after_restart(tmp_path):
    asyncio.run(_test_session_restores_pending_events_after_restart(tmp_path))


async def _test_session_restores_pending_events_after_restart(tmp_path):
    """A trigger persisted before its consumer ran resumes after a restart."""
    from reactifact.resources import RuntimeResources

    backend = FileKVBackend(str(tmp_path / "sessions"))
    store = SessionStore(backend)
    session = await store.open("resume", resources=RuntimeResources())
    session.context.create(Question(text="resume me"))
    await session.save()  # the app persisted the user's message; no agent has run

    # process restart: a fresh store and Context — only what was saved exists
    session2 = await store.open("resume")
    assert session2.context.list_artifacts(Answer) == []

    runtime = Runtime(session2.context, agents=[SimpleAnswerer()], session=session2)
    runs = await runtime.arun()

    assert runs >= 1
    assert session2.context.list_artifacts(Answer)[0].data.text == "RESUME ME"


def test_settled_run_does_not_replay_on_restart(tmp_path):
    asyncio.run(_test_settled_run_does_not_replay_on_restart(tmp_path))


async def _test_settled_run_does_not_replay_on_restart(tmp_path):
    """A completed run leaves no replayable triggers behind."""
    from reactifact.resources import RuntimeResources

    backend = FileKVBackend(str(tmp_path / "sessions"))
    store = SessionStore(backend)
    session = await store.open("settled", resources=RuntimeResources())
    runtime = Runtime(session.context, agents=[SimpleAnswerer()], session=session)
    session.context.create(Question(text="once"))
    await runtime.arun()
    assert len(session.context.list_artifacts(Answer)) == 1

    session2 = await store.open("settled")
    runtime2 = Runtime(session2.context, agents=[SimpleAnswerer()], session=session2)
    runs = await runtime2.arun()

    assert runs == 0
    assert len(session2.context.list_artifacts(Answer)) == 1
