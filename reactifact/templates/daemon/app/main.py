"""Daemon entry point: run one tick every `APP_INTERVAL_SECONDS`, gracefully.

python -m app.main
"""

from __future__ import annotations

import asyncio
import logging

from reactifact import Daemon

from app.config import Settings, get_settings
from app.runner import run_once

logger = logging.getLogger("daemon")


async def serve(settings: Settings) -> int:
    async def tick(sequence: int) -> None:
        report = await run_once(settings, sequence)
        if report is not None:
            logger.info("tick %s: %s", report.sequence, report.text)

    daemon = Daemon(
        tick=tick,
        interval=settings.interval_seconds,
        run_once=settings.run_once,
    )
    ticks = await daemon.serve()
    logger.info("daemon stopped after %s tick(s)", ticks)
    return ticks


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
