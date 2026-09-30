"""outbox example: one intent, one send across retries, replays and merges."""

import asyncio

from examples.outbox.main import (
    ACTION,
    app_owned_retry,
    branches,
    failure_retry,
    happy_path,
    rederivation,
    replay,
    same_generation,
)
from reactifact import PendingAction


def test_happy_path_sends_once():
    context, log = asyncio.run(happy_path())
    action = context.get(ACTION)
    assert action is not None
    assert action.data.status == "dispatched"
    assert log.keys() == ["notify:42"]


def test_rederivation_does_not_resend():
    first, second = asyncio.run(rederivation())
    assert (first, second) == (1, 0)


def test_same_generation_dedupes():
    context, log = asyncio.run(same_generation())
    assert len(log) == 1
    assert len(context.list_artifacts(PendingAction)) == 1


def test_merged_branches_deliver_once():
    merged, log, pending = asyncio.run(branches())
    assert len(log) == 1
    assert pending == 0
    assert len(merged.list_artifacts(PendingAction)) == 1


def test_replay_does_not_resend():
    before, after, summary = asyncio.run(replay())
    assert before == 1 and after == before
    assert summary["dispatched_actions"] == 1
    assert summary["pending_actions"] == 0


def test_failure_is_recorded_and_retry_delivers_once():
    context, log, raised, failed = asyncio.run(failure_retry())
    action = context.get("action:notify:55")
    assert failed == "failed"
    assert "smtp" in raised
    assert action is not None
    assert action.data.status == "dispatched"
    assert log.keys() == ["notify:55"]


def test_app_owned_retry_absorbs_transient_failures():
    context, log, status, attempts = asyncio.run(app_owned_retry())
    assert status == "dispatched"
    assert attempts == 0  # the wrapper absorbed the failures, runtime saw one outcome
    assert log.keys() == ["notify:77"]
