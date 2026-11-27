"""`reactifact doctor` — configuration and common-boundary checks.

It is advisory by design: it inspects a settings/app factory by duck typing and
scans the project for a few common mistakes. It never starts the server.
"""

from __future__ import annotations

import argparse
import ast
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


def _callable_without_required_args(target: Any) -> bool:
    try:
        params = inspect.signature(target).parameters.values()
    except (TypeError, ValueError):
        return False
    return not any(
        param.default is inspect.Parameter.empty
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for param in params
    )


def _load_target(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attr) if attr else module
    if (
        callable(target)
        and not inspect.isclass(target)
        and _callable_without_required_args(target)
    ):
        target = target()
    state = getattr(target, "state", None)
    if state is not None and (
        hasattr(state, "settings") or hasattr(state, "assistant")
    ):
        return state
    return target


def _has_removed_usage(path: Path) -> bool:
    """AST scan: a real `Produce`/`@produce`/`also_creates` use, not a docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"Produce", "ProduceCall"}:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                if isinstance(target, ast.Name) and target.id == "produce":
                    return True
        if isinstance(node, ast.Call) and any(
            keyword.arg in {"also_creates", "reacts_to"} for keyword in node.keywords
        ):
            return True
    return False


def _scan_sources(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    sources = list(_py_files(root))
    if not sources:
        return findings
    text = "\n".join(path.read_text(errors="ignore") for path in sources)
    if "scope_session" not in text and "session_key" not in text:
        findings.append(
            Finding(
                "WARN",
                "no session scoping helper found (client id may be a storage key)",
            )
        )
    if "lifespan" not in text and "aclose" not in text:
        findings.append(Finding("WARN", "no lifespan/resource cleanup found"))
    if any(_has_removed_usage(path) for path in sources):
        findings.append(
            Finding("WARN", "removed Produce/@produce usage found; use Step/@step")
        )
    return findings


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

    findings.extend(_scan_sources(root))
    return findings


def _looks_like_settings(value: Any) -> bool:
    return hasattr(value, "llm_provider") or (
        hasattr(value, "env") and hasattr(value, "auth_token")
    )


def check_runtime(target: Any, *, root: Path) -> list[Finding]:
    """Checks a `Chat`/`Pipeline`/`RuntimeResources` object (not a settings model)."""
    findings: list[Finding] = []
    resources = getattr(target, "resources", None)
    if resources is None and hasattr(target, "llm") and hasattr(target, "register"):
        resources = target
    assistant = getattr(target, "assistant", None)
    budget = getattr(target, "budget", None)
    if budget is None:
        budget = getattr(assistant, "_budget", None)
    llm = getattr(resources, "llm", None) if resources is not None else None
    if llm is None:
        llm = getattr(target, "llm", None)
    memory = getattr(assistant, "_memory", None)

    findings.append(
        Finding("OK", "llm: configured" if llm is not None else "llm: offline/fallback")
    )
    if budget is None:
        findings.append(Finding("WARN", "no budget limits configured"))
    else:
        findings.append(Finding("OK", "budget limits configured"))
    if assistant is not None:
        findings.append(
            Finding(
                "OK" if memory is not None else "WARN",
                "chat memory is bounded"
                if memory is not None
                else "chat history is unbounded (no ChatMemory policy)",
            )
        )
    findings.extend(_scan_sources(root))
    return findings


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        target = _load_target(args.target)
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        print(f"reactifact doctor: cannot load {args.target!r}: {exc}")
        return 1

    root = Path(args.path).expanduser().resolve()
    findings: list[Finding] = []
    settings = target if _looks_like_settings(target) else None
    if settings is None:
        settings = getattr(target, "settings", None)
    if settings is not None:
        findings.extend(check_settings(settings, root=root))
    elif (
        hasattr(target, "assistant")
        or hasattr(target, "resources")
        or hasattr(target, "llm")
    ):
        findings.extend(check_runtime(target, root=root))
    else:
        findings.append(
            Finding(
                "WARN",
                "target is not a settings or Chat/Pipeline/resources object; "
                "pass app.config:get_settings or a zero-arg factory",
            )
        )
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


__all__ = ["Finding", "add_parser", "check_runtime", "check_settings"]
