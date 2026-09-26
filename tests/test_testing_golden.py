"""`reactifact.testing` golden snapshots — `assert_golden_file` (seed on first
run, fail on drift, `update`/`$REACTIFACT_GOLDEN_UPDATE` to rewrite) and the
`ScenarioResult`/`Scenario` convenience wrappers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Produce,
    ProduceCall,
)
from reactifact.audit import context_hash
from reactifact.testing import ScenarioLab, assert_golden_file
from reactifact.testing.exceptions import AssertionFailure
from reactifact.testing.golden import GOLDEN_UPDATE_ENV_VAR, GoldenRun


class Reply(BaseModel):
    text: str


def _context(n: int = 21) -> Context:
    ctx = Context()
    ctx.create(Reply(text=str(n * 2)), id="out")
    return ctx


def test_golden_run_save_load_roundtrip(tmp_path):
    path = tmp_path / "g.json"
    original = GoldenRun(context_sha256="abc", prompt_hashes=("p1", "p2"))

    original.save(path)

    assert GoldenRun.load(path) == original


def test_assert_golden_file_seeds_then_matches(tmp_path):
    path = tmp_path / "g.json"

    seeded = assert_golden_file(_context(21), path)

    assert path.exists()
    assert seeded.context_sha256 == context_hash(_context(21))
    assert_golden_file(_context(21), path)  # second run compares and passes


def test_assert_golden_file_fails_on_drift(tmp_path):
    path = tmp_path / "g.json"
    assert_golden_file(_context(21), path)

    with pytest.raises(AssertionFailure, match="drifted"):
        assert_golden_file(_context(22), path)


def test_assert_golden_file_update_rewrites(tmp_path):
    path = tmp_path / "g.json"
    assert_golden_file(_context(21), path)

    assert_golden_file(_context(22), path, update=True)

    assert_golden_file(_context(22), path)  # the new value is the baseline
    with pytest.raises(AssertionFailure):
        assert_golden_file(_context(21), path)


def test_assert_golden_file_update_env_var(monkeypatch, tmp_path):
    path = tmp_path / "g.json"
    assert_golden_file(_context(21), path)

    monkeypatch.setenv(GOLDEN_UPDATE_ENV_VAR, "1")
    assert_golden_file(_context(22), path)  # rewritten, no drift failure

    monkeypatch.delenv(GOLDEN_UPDATE_ENV_VAR)
    assert_golden_file(_context(22), path)
    with pytest.raises(AssertionFailure):
        assert_golden_file(_context(21), path)


class Seed(BaseModel):
    n: int = 1


class Out(BaseModel):
    value: int


class Doubler(Produce[Out]):
    artifact_type = Out

    async def produce(self, call: ProduceCall) -> None:
        seed = next((a for a in call.inputs if isinstance(a.data, Seed)), None)
        if seed is None:
            return None
        call.effects.create(Out(value=seed.data.n * 2), id="out")


class DoublerAgent(Agent):
    name = "doubler"
    consumes = [Consume(Seed)]
    produces = [Doubler()]


def test_scenario_result_golden_seeds_then_matches(tmp_path):
    path = tmp_path / "run.golden.json"
    result = ScenarioLab([DoublerAgent()]).run_sync(Seed(n=21))

    seeded = result.assert_golden(path)

    assert seeded.context_sha256 == result.context_hash
    result.assert_golden(path)  # the same run/context matches its own snapshot


def test_scenario_result_golden_detects_drift(tmp_path):
    path = tmp_path / "run.golden.json"
    lab = ScenarioLab([DoublerAgent()])
    lab.run_sync(Seed(n=21)).assert_golden(path)

    with pytest.raises(AssertionFailure, match="drifted"):
        lab.run_sync(Seed(n=22)).assert_golden(path)


def test_scenario_multi_turn_golden(tmp_path):
    path = tmp_path / "convo.golden.json"
    convo = ScenarioLab([DoublerAgent()]).scenario()
    convo.turn_sync(Seed(n=1))
    convo.turn_sync(Seed(n=2))

    seeded = convo.assert_golden(path)

    assert seeded.context_sha256 == context_hash(convo.context)
    convo.assert_golden(path)
