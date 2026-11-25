"""`reactifact doctor` — configuration and common-boundary checks.

It is advisory by design: it inspects a settings/app factory by duck typing and
scans the project for a few common mistakes. It never starts the server.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULTS = "app.config:get_settings"
_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


@dataclass
class Finding:
    level: str  # "OK" | "WARN" | "ERROR"
    message: str


def _is_set(value: Any) -> bool:
    if value is None:
        return False
    if hasattr(value, "get_secret_value"):
        return bool(value.get_secret_value())
    return bool(str(value))


def _load_target(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attr) if attr else module
    if callable(target) and not inspect.signature(target).parameters:
        target = target()
    state = getattr(target, "state", None)
    if state is not None and hasattr(state, "settings"):
        return state.settings
    return target


def _py_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def check_settings(settings: Any, *, root: Path) -> list[Finding]:
    findings: list[Finding] = []

    def add(level: str, message: str) -> None:
        findings.append(Finding(level, message))

    env = getattr(settings, "env", "dev")
    auth = getattr(settings, "auth_token", None)
    if not _is_set(auth):
        add("ERROR", "auth token is not configured (fail-closed web app)")
    else:
        add("OK", "auth token is configured")

    session_backend = getattr(settings, "session_backend", None)
    if env == "prod" and session_backend == "memory":
        add("ERROR", "session_backend=memory is not allowed in prod")
    elif session_backend:
        add("OK", f"session backend: {session_backend}")

    trace_backend = getattr(settings, "trace_backend", None)
    if env == "prod" and trace_backend in {None, "memory"}:
        add("ERROR", "trace store must be persistent in prod")
    elif trace_backend:
        add("OK", f"trace backend: {trace_backend}")

    provider = str(getattr(settings, "llm_provider", "none"))
    model = getattr(settings, "llm_model", None)
    if provider == "none":
        add("OK", "llm_provider=none (offline/fallback mode)")
    elif not model:
        add("ERROR", f"llm_provider={provider!r} but no llm_model")
    else:
        add("OK", f"llm: {provider} / {model}")
        key_env = getattr(settings, "llm_api_key_env", None)
        if key_env and not os.getenv(str(key_env)):
            add("ERROR", f"{key_env} is not set")

    budget_values = [
        getattr(settings, f"budget_max_{name}", None)
        for name in ("runs", "iterations", "seconds", "tool_calls", "tokens", "cost")
    ]
    if not any(value is not None for value in budget_values):
        add("WARN", "no budget limits configured")
    else:
        add("OK", "budget limits configured")

    if (
        getattr(settings, "chat_memory_keep", None) is None
        and getattr(settings, "chat_memory_commits", None) is None
    ):
        add("WARN", "chat history is unbounded (no ChatMemory policy)")

    sources = list(_py_files(root))
    if sources:
        text = "\n".join(path.read_text(errors="ignore") for path in sources)
        if "scope_session" not in text and "session_key" not in text:
            add(
                "WARN",
                "no session scoping helper found (client id may be a storage key)",
            )
        if "lifespan" not in text and "aclose" not in text:
            add("WARN", "no lifespan/resource cleanup found")
        if "@produce" in text or "Produce" in text:
            add("WARN", "removed Produce/@produce usage found; use Step/@step")

    return findings


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        target = _load_target(args.target)
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        print(f"reactifact doctor: cannot load {args.target!r}: {exc}")
        return 1

    findings = check_settings(target, root=Path(args.path).expanduser().resolve())
    print(f"reactifact doctor — {args.target}")
    for finding in findings:
        print(f"  {finding.level:<5} {finding.message}")
    errors = sum(finding.level == "ERROR" for finding in findings)
    warnings = sum(finding.level == "WARN" for finding in findings)
    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("doctor", help="check app settings and common boundaries")
    p.add_argument(
        "target",
        nargs="?",
        default=_DEFAULTS,
        help=f"module:factory (default: {_DEFAULTS})",
    )
    p.add_argument("--path", default=".", help="project directory to scan")
    p.set_defaults(func=cmd_doctor)


__all__ = ["Finding", "add_parser", "check_settings"]
