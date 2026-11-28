"""reactifact.logging: correlation, formatters, one-call configuration."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import contextmanager

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Patch,
    Runtime,
    RuntimeResources,
    configure_logging,
    get_logger,
)
from reactifact.logging import JSONFormatter, bind, current_context

ROOT = logging.getLogger("reactifact")


@pytest.fixture(autouse=True)
def _restore_logging():
    """Configuration is global; snapshot and restore so tests never interfere."""
    level, propagate = ROOT.level, ROOT.propagate
    handlers = list(ROOT.handlers)
    try:
        yield
    finally:
        ROOT.setLevel(level)
        ROOT.propagate = propagate
        for handler in list(ROOT.handlers):
            ROOT.removeHandler(handler)
        for handler in handlers:
            ROOT.addHandler(handler)


@contextmanager
def capture(logger_name: str):
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def test_bind_injects_correlation_fields():
    with capture("reactifact.test") as records:
        with bind(tenant="acme", request_id="r-1"):
            assert current_context() == {"tenant": "acme", "request_id": "r-1"}
            get_logger("reactifact.test").info("hello")
        assert current_context() == {}
    assert records[0].tenant == "acme"
    assert records[0].request_id == "r-1"


def test_json_formatter_flattens_extras_and_correlation():
    with capture("reactifact.test") as records, bind(run_id="run-7", agent="verifier"):
        get_logger("reactifact.test").warning("agent failed", extra={"error": "x"})
    payload = json.loads(JSONFormatter().format(records[0]))
    assert payload["message"] == "agent failed"
    assert payload["level"] == "WARNING"
    assert payload["run_id"] == "run-7"
    assert payload["agent"] == "verifier"
    assert payload["error"] == "x"


def test_configure_logging_human_appends_correlation_suffix():
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    with bind(run_id="run-9"):
        get_logger("reactifact.demo").info("started")
    out = stream.getvalue()
    assert "started" in out
    assert "run_id=run-9" in out


def test_configure_logging_json_writes_parseable_lines():
    stream = io.StringIO()
    configure_logging(level="INFO", json=True, stream=stream)
    with bind(session_id="s-1"):
        get_logger("reactifact.demo").info("hello", extra={"n": 3})
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "hello"
    assert payload["session_id"] == "s-1"
    assert payload["n"] == 3


def test_configure_logging_is_idempotent():
    configure_logging(level="INFO", stream=io.StringIO())
    configure_logging(level="DEBUG", stream=io.StringIO())
    marked = [
        handler
        for handler in ROOT.handlers
        if getattr(handler, "_reactifact_handler", False)
    ]
    assert len(marked) == 1
    assert ROOT.level == logging.DEBUG


class _Question(BaseModel):
    text: str


class _Answer(BaseModel):
    text: str


class _Echo(Agent):
    consumes = [Consume(_Question)]
    produces = []

    async def run(self, event, context):
        return Patch().create(_Answer(text="x"))


def test_runtime_emits_correlated_run_logs():
    with capture("reactifact.runtime") as records:
        context = Context(resources=RuntimeResources())
        context.create(_Question(text="hi"))
        asyncio.run(Runtime(context, agents=[_Echo()]).arun())

    finished = [r for r in records if r.getMessage() == "run finished"]
    assert finished, [r.getMessage() for r in records]
    record = finished[0]
    assert record.outcome == "completed"
    assert record.runs == 1
    assert record.run_id  # correlated to the turn
