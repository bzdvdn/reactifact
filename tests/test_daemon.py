"""`reactifact.daemon.Daemon` — loop, seed source, errors and shutdown."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Daemon, Pipeline, step


class Tick(BaseModel):
    text: str


class Report(BaseModel):
    text: str


@step(consumes=Tick)
def report(tick: Tick) -> Report:
    return Report(text=tick.text.upper())


def _pipeline() -> Pipeline:
    return Pipeline(steps=[report], seed_type=Tick, answer_type=Report)


def test_pipeline_ticks_until_max_ticks():
    seen: list[int] = []
    pipeline = _pipeline()

    async def seed_source(sequence: int) -> Tick:
        seen.append(sequence)
        return Tick(text=f"tick-{sequence}")

    daemon = Daemon(
        pipeline,
        seed_source=seed_source,
        interval=0,
        max_ticks=3,
        install_signal_handlers=False,
    )

    assert asyncio.run(daemon.serve()) == 3
    assert seen == [0, 1, 2]
    assert pipeline.last_result is not None


def test_none_seed_skips_a_tick():
    pipeline = _pipeline()
    seen: list[int] = []

    def seed_source(sequence: int) -> Tick | None:
        seen.append(sequence)
        return None if sequence == 0 else Tick(text="ok")

    daemon = Daemon(
        pipeline,
        seed_source=seed_source,
        interval=0,
        max_ticks=2,
        install_signal_handlers=False,
    )

    assert asyncio.run(daemon.serve()) == 2
    assert seen == [0, 1]
    assert pipeline.last_result is not None


def test_custom_tick_and_run_once():
    calls: list[int] = []

    async def tick(sequence: int) -> None:
        calls.append(sequence)

    daemon = Daemon(
        tick=tick,
        interval=0,
        run_once=True,
        install_signal_handlers=False,
    )

    assert asyncio.run(daemon.serve()) == 1
    assert calls == [0]


def test_errors_continue_or_raise():
    calls: list[int] = []

    async def tick(sequence: int) -> None:
        calls.append(sequence)
        if sequence == 0:
            raise RuntimeError("boom")

    continuing = Daemon(
        tick=tick,
        interval=0,
        max_ticks=2,
        install_signal_handlers=False,
    )
    assert asyncio.run(continuing.serve()) == 2
    assert calls == [0, 1]

    raising = Daemon(
        tick=tick,
        interval=0,
        run_once=True,
        raise_on_error=True,
        install_signal_handlers=False,
    )
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(raising.serve())


def test_daemon_requires_a_tick_or_pipeline_and_seed_source():
    with pytest.raises(TypeError):
        Daemon()
    with pytest.raises(TypeError):
        Daemon(_pipeline())
    with pytest.raises(TypeError):
        Daemon(tick=lambda _i: None, pipeline=_pipeline(), seed_source=lambda _i: None)


def test_stop_before_serve_does_nothing():
    daemon = Daemon(
        tick=lambda _i: None,
        max_ticks=5,
        install_signal_handlers=False,
    )
    daemon.stop()

    assert asyncio.run(daemon.serve()) == 0
