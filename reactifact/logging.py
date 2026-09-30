"""reactifact.logging — correlated, structured logging out of the box.

Library etiquette: reactifact never configures the root logger and never writes
to stderr on import — it attaches a `NullHandler` (silent until you opt in) and
exposes one call to turn logs on:

    from reactifact import configure_logging

    configure_logging()                    # INFO, human-readable
    configure_logging(json=True)           # one JSON object per line
    configure_logging(level="DEBUG", stream=sys.stderr)

Every record carries the current turn's correlation fields — `run_id`,
`session_id`, `generation`, `agent`, plus any app fields bound with
`bind(...)` — so a line can be tied back to the exact run/agent that produced
it. Logging complements tracing: a `RunTrace` is the durable audit of a run;
these logs are what an operator tails while it happens.

Nothing here logs artifact data or prompt contents: logs are metadata. Use
`bind` to add domain fields (`tenant`, `user`, `request_id`) from app code.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

#: The logger namespace the framework writes under (configurable as a whole).
ROOT = "reactifact"

#: Fields injected automatically by the runtime and worth surfacing in the
#: human-readable formatter as a trailing `[key=value …]` suffix.
_CORRELATION_KEYS = ("run_id", "session_id", "generation", "agent", "request_id")

#: `LogRecord` attributes that are *not* treated as structured extras.
_STANDARD: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

#: Correlation context, inherited by the child tasks a generation fans out to.
_CONTEXT: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "reactifact_log_context", default=None
)


# --------------------------------------------------------------------------- #
# Correlation context
# --------------------------------------------------------------------------- #


def set_context(**fields: Any) -> Token[Mapping[str, Any] | None]:
    """Installs `fields` as the log correlation context; returns a reset token.

    Internal entry point (the runtime calls it once per turn); app code should
    prefer the `bind(...)` context manager.
    """
    return _CONTEXT.set({**(_CONTEXT.get() or {}), **fields})


def reset_context(token: Token[Mapping[str, Any] | None]) -> None:
    """Restores the previous correlation context (call in a `finally`)."""
    _CONTEXT.reset(token)


def current_context() -> Mapping[str, Any]:
    """The active correlation fields (empty outside a run)."""
    return _CONTEXT.get() or {}


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Add correlation fields for the duration of the block.

    with bind(tenant="acme", request_id=rid):
        logger.info("handling request")
    """
    token = set_context(**fields)
    try:
        yield
    finally:
        reset_context(token)


class _ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    """A logger that merges the correlation context into every record."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:  # noqa: ANN401 — logging's own signature
        extra = {**(_CONTEXT.get() or {}), **(self.extra or {})}
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """A `reactifact.*` logger that auto-injects the correlation fields.

    `name` is the plain module name (e.g. `__name__`); pass `None`-ish app codes
    as `bind(...)` fields rather than composing names.
    """
    return _ContextAdapter(logging.getLogger(name), {})


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    extras = {
        key: value for key, value in record.__dict__.items() if key not in _STANDARD
    }
    return {key: extras[key] for key in sorted(extras)}


class JSONFormatter(logging.Formatter):
    """One JSON object per record, correlation fields flattened in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _HumanFormatter(logging.Formatter):
    """`time level logger — message  [run_id=… agent=…]` (suffix only if present)."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _extras(record)
        if not extras:
            return base
        suffix = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{base}  [{suffix}]"


# --------------------------------------------------------------------------- #
# One-call configuration
# --------------------------------------------------------------------------- #


def configure_logging(
    level: str | int = "INFO",
    *,
    json: bool = False,  # noqa: A002 — the public flag name
    stream: Any = None,
) -> logging.Logger:
    """Turn reactifact logging on. Idempotent; returns the `reactifact` logger.

    - `level` — the `reactifact` logger level (`"INFO"` default).
    - `json` — emit JSON lines instead of the human format.
    - `stream` — destination (default: `sys.stderr`).

    Stops propagation to the root logger, so it composes cleanly with an app's
    own `basicConfig`/handlers without double output.
    """
    logger = logging.getLogger(ROOT)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_reactifact_handler", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler._reactifact_handler = True  # type: ignore[attr-defined]
    if json:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            _HumanFormatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s")
        )
    logger.addHandler(handler)
    return logger


# Silent by default: a library must not write to stderr unless asked to.
logging.getLogger(ROOT).addHandler(logging.NullHandler())


__all__ = [
    "JSONFormatter",
    "bind",
    "configure_logging",
    "current_context",
    "get_logger",
    "reset_context",
    "set_context",
]
