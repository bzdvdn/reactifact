"""`reactifact dev` — run an ASGI app and point at its contracts and traces.

The FastAPI-template equivalent of `fastapi dev`: one command, reloads on
change, and prints the three URLs worth opening. The app is imported by target
(`module:attr`), so reload works the same way uvicorn's own `--reload` does.

    reactifact dev                          # app.main:create_app or app.main:app
    reactifact dev examples.devops.web:app  # any ASGI app
    reactifact dev app.main:create_app --no-reload --port 9000
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import threading
import webbrowser
from functools import partial
from pathlib import Path
from typing import Any

_DEFAULT_TARGET = "app.main:create_app"


def _import_target(spec: str) -> tuple[str, Any]:
    """Imports `module` or `module:attr`; with no attr, tries create_app/app."""
    module_name, has_attr, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"reactifact dev: could not import {module_name!r}: {exc}. "
            "Run it from the project directory (or install the package)."
        ) from exc

    if has_attr:
        try:
            return attr, getattr(module, attr)
        except AttributeError as exc:
            raise SystemExit(
                f"reactifact dev: {spec!r} not found in {module_name!r}"
            ) from exc

    for candidate in ("create_app", "app"):
        if hasattr(module, candidate):
            return candidate, getattr(module, candidate)
    raise SystemExit(
        f"reactifact dev: no 'create_app' or 'app' attribute in {module_name!r}; "
        f"pass an explicit target like {module_name}:app"
    )


def _is_factory(obj: Any) -> bool:
    """Whether uvicorn should call `obj()` instead of treating it as an app."""
    return (
        inspect.isclass(obj)
        or inspect.isfunction(obj)
        or inspect.ismethod(obj)
        or isinstance(obj, partial)
    )


def _resolve(spec: str) -> tuple[str, bool]:
    """Returns the import string uvicorn needs and whether it is a factory."""
    module_name, _, _ = spec.partition(":")
    attr, obj = _import_target(spec)
    return f"{module_name}:{attr}", _is_factory(obj)


def _open_later(url: str, delay: float = 1.5) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def cmd_dev(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            'reactifact dev needs uvicorn; install it with `uv add "reactifact[web]"`'
        ) from None

    target, factory = _resolve(args.target)
    if args.factory is not None:
        factory = args.factory

    host = args.host or os.environ.get("HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("PORT", "8000"))
    base = f"http://{host}:{port}"

    print(f"reactifact dev  {target}" + (" (factory)" if factory else ""))
    print(f"  app        {base}")
    print(f"  contracts  {base}/contracts   (if mounted)")
    print(f"  traces     {base}/traces      (if mounted)")
    if not Path(".env").exists() and Path(".env.example").exists():
        print("  note       no .env here; copy .env.example to .env if startup fails")
    print(flush=True)

    if args.open is not None:
        path = args.open if args.open.startswith("/") else f"/{args.open}"
        _open_later(f"{base}{path}")

    uvicorn.run(
        target,
        factory=factory,
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_dev = sub.add_parser(
        "dev",
        help="run an ASGI app with reload and contracts/traces URLs",
    )
    p_dev.add_argument(
        "target",
        nargs="?",
        default=_DEFAULT_TARGET,
        help=f"ASGI app or factory (default: {_DEFAULT_TARGET})",
    )
    p_dev.add_argument("--host", default=None, help="bind host (default: 127.0.0.1)")
    p_dev.add_argument(
        "--port", type=int, default=None, help="bind port (default: 8000)"
    )
    p_dev.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="reload on file changes (default: on)",
    )
    p_dev.add_argument(
        "--factory",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="force factory mode on/off (default: infer from the target)",
    )
    p_dev.add_argument(
        "--open",
        nargs="?",
        const="/contracts",
        default=None,
        metavar="PATH",
        help="open this path in a browser once the server starts (default: /contracts)",
    )
    p_dev.set_defaults(func=cmd_dev)


__all__ = ["add_parser"]
