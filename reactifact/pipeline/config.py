"""`RunConfig`: runtime settings for `PipelineRunner`."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent import Agent
from ..budget import Budget
from ..events import Event
from ..providers import LLMProvider
from ..resources import RuntimeResources
from .schedule import Schedule


@dataclass
class RunConfig:
    """Runtime settings for `PipelineRunner`.

    `Pipeline` owns the definition; this object owns resources and execution
    policy. All fields are optional and fall back to `Runtime` defaults.
    """

    resources: RuntimeResources | None = None
    llm: LLMProvider | None = None
    budget: Budget | None = None
    tracer: Any = None
    max_concurrency: int | None = None
    isolate_errors: bool | None = None
    on_agent_error: Callable[[Agent, Event, BaseException], None] | None = None
    lifespan: Callable[[], AbstractAsyncContextManager[RuntimeResources]] | None = None
    traces: bool | str | Path | Any | None = None
    trace_sample: Any = None
    schedule: Schedule | None = None


__all__ = ["RunConfig"]
