"""A small, transport-neutral scheduler for a Pipeline or a custom tick.

    from reactifact import Daemon

    Daemon(
        pipeline,
        seed_source=next_tick,   # sync/async: tick index -> seed | None
        config=RunConfig(traces=True),
        interval=60,
        run_once=False,
    ).serve()

`Daemon` owns only the loop: interval, graceful shutdown, per-tick error
isolation and `run_once`/`max_ticks`. `tick=` is the generic form for apps
that already have their own per-tick function (the bundled daemon template);
`pipeline=` + `seed_source=` is the short form when a seed model is enough.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from .pipeline import PipelineDefinition, PipelineRunner, RunConfig

logger = logging.getLogger("reactifact.daemon")

#: A per-tick source: may be sync or async; `None` skips the tick.
SeedSource = Callable[[int], Any | Awaitable[Any]]
#: A fully custom tick, e.g. `lambda sequence: run_once(settings, sequence)`.
TickFn = Callable[[int], Any | Awaitable[Any]]


class Daemon:
    """Run a Pipeline (or a custom tick) on an interval with clean shutdown."""

    def __init__(
        self,
        pipeline: PipelineDefinition | PipelineRunner | None = None,
        *,
        config: RunConfig | None = None,
        seed_source: SeedSource | None = None,
        tick: TickFn | None = None,
        interval: float = 60.0,
        run_once: bool = False,
        max_ticks: int | None = None,
        raise_on_error: bool = False,
        install_signal_handlers: bool = True,
        stop_event: asyncio.Event | None = None,
    ):
        self._tick: TickFn | None = None
        self.pipeline: PipelineDefinition | None = None
        self.runner: PipelineRunner | None = None
        self.seed_source: SeedSource | None = None
        if tick is not None:
            if pipeline is not None or seed_source is not None:
                raise TypeError("pass either tick= or pipeline= + seed_source=")
            self._tick = tick
        else:
            if pipeline is None or seed_source is None:
                raise TypeError("Daemon requires tick= or pipeline= + seed_source=")
            if isinstance(pipeline, PipelineRunner):
                if config is not None:
                    raise TypeError("Pass config= only with a Pipeline, not a runner")
                self.runner = pipeline
            else:
                self.runner = PipelineRunner(pipeline, config=config)
            self.pipeline = self.runner.pipeline
            self.seed_source = seed_source
        if interval < 0:
            raise ValueError("interval must be >= 0")
        if max_ticks is not None and max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")
        self.interval = interval
        self.run_once = run_once
        self.max_ticks = max_ticks
        self.raise_on_error = raise_on_error
        self.install_signal_handlers = install_signal_handlers
        self._stop_event = stop_event
        #: Number of ticks attempted by the most recent `serve()`.
        self.ticks = 0

    @property
    def stop_event(self) -> asyncio.Event:
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    def stop(self) -> None:
        """Ask `serve()` to stop after the current tick."""
        self.stop_event.set()

    async def tick(self, sequence: int) -> Any:
        """Run one tick; `None` from a `seed_source` means "nothing to do"."""
        if self._tick is not None:
            result = self._tick(sequence)
        else:
            assert self.runner is not None and self.seed_source is not None
            seed = self.seed_source(sequence)
            if inspect.isawaitable(seed):
                seed = await seed
            if seed is None:
                return None
            result = await self.runner.arun(seed)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def serve(self) -> int:
        """Run ticks until stopped; returns the number of ticks attempted."""
        stop = self.stop_event
        if self.install_signal_handlers:
            self._install_signal_handlers(stop)

        limit = 1 if self.run_once else self.max_ticks
        self.ticks = 0
        while not stop.is_set():
            if limit is not None and self.ticks >= limit:
                break
            sequence = self.ticks
            self.ticks += 1
            try:
                result = await self.tick(sequence)
            except Exception:
                logger.exception("daemon tick %s failed", sequence)
                if self.raise_on_error:
                    raise
            else:
                if result is not None:
                    logger.info("tick %s: %s", sequence, _summary(result))

            if limit is not None and self.ticks >= limit:
                break
            if stop.is_set():
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
        return self.ticks

    @staticmethod
    def _install_signal_handlers(stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(sig, stop.set)


def _summary(result: Any) -> str:
    """A short log line; full result objects are not dumped into logs."""
    outcome = getattr(result, "outcome", None)
    runs = getattr(result, "runs", None)
    if outcome is not None and runs is not None:
        return f"outcome={getattr(outcome, 'value', outcome)} runs={runs}"
    return type(result).__name__


__all__ = ["Daemon"]
