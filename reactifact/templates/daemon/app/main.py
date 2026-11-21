"""Daemon entry point: run one tick every `APP_INTERVAL_SECONDS`, gracefully.

python -m app.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from app.config import Settings, get_settings
from app.runner import run_once

logger = logging.getLogger("daemon")


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows / non-main thread: rely on KeyboardInterrupt instead.
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


async def serve(settings: Settings) -> int:
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    sequence = 0
    while not stop.is_set():
        report = await run_once(settings, sequence)
        if report is not None:
            logger.info("tick %s: %s", report.sequence, report.text)
        sequence += 1
        if settings.run_once:
            break
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.interval_seconds)
    logger.info("daemon stopped after %s tick(s)", sequence)
    return sequence


async def main() -> int:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.validate_runtime()
    return await serve(settings)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
