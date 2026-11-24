"""`reactifact run` — run a `Pipeline` with one seed artifact."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..pipeline import Pipeline


def _load_attr(spec: str) -> Any:
    module_name, has_attr, attr = spec.partition(":")
    if not has_attr:
        raise SystemExit(f"expected 'module:attr', got {spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise SystemExit(f"could not import {module_name!r}: {exc}") from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise SystemExit(f"{spec!r} not found") from exc


def _load_pipeline(spec: str) -> Pipeline:
    value = _load_attr(spec)
    if not isinstance(value, Pipeline):
        raise SystemExit(
            f"{spec!r} must resolve to a Pipeline, got {type(value).__name__}"
        )
    return value


def _load_model(spec: str) -> type[BaseModel]:
    value = _load_attr(spec)
    if not (isinstance(value, type) and issubclass(value, BaseModel)):
        raise SystemExit(f"{spec!r} must resolve to a pydantic model")
    return value


def cmd_run(args: argparse.Namespace) -> int:
    pipeline = _load_pipeline(args.pipeline)

    seed_type = _load_model(args.seed) if args.seed else pipeline.seed_type
    if seed_type is None:
        raise SystemExit(
            "pass --seed module:Model (or set seed_type= on the Pipeline) "
            "so the CLI knows how to build the seed artifact"
        )

    if args.json and args.json_file:
        raise SystemExit("pass either --json or --json-file, not both")
    if args.json or args.json_file:
        raw = args.json
        if args.json_file:
            raw = Path(args.json_file).read_text(encoding="utf-8")
        seed: BaseModel = seed_type.model_validate_json(raw)
    elif args.text is None:
        raise SystemExit("pass a seed text argument, --json or --json-file")
    else:
        seed = seed_type(**{pipeline.seed_field: args.text})

    result = pipeline.run(seed)
    show = _load_model(args.show) if args.show else pipeline.answer_type
    if show is not None:
        artifact = result.latest(show)
        if artifact is None:
            print(f"no {show.__name__} artifact produced", file=sys.stderr)
            return 1
        print(
            json.dumps(
                artifact.data.model_dump(mode="json"), indent=2, ensure_ascii=False
            )
        )
        return 0

    print(f"runs: {result.runs}  outcome: {result.outcome.value}  hash: {result.hash}")
    for artifact in result.context.list_artifacts():
        print(f"  {type(artifact.data).__name__:<24} {artifact.id}")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_run = sub.add_parser("run", help="run a Pipeline with one seed artifact")
    p_run.add_argument("pipeline", help="module:attr resolving to a Pipeline")
    p_run.add_argument("text", nargs="?", help="seed text (uses Pipeline.seed_type)")
    p_run.add_argument("--seed", metavar="MODEL", help="module:Model for the seed")
    p_run.add_argument("--json", help="seed as JSON")
    p_run.add_argument("--json-file", help="read the seed JSON from a file")
    p_run.add_argument(
        "--show",
        metavar="MODEL",
        help="print the latest artifact of this model (default: Pipeline.answer_type)",
    )
    p_run.set_defaults(func=cmd_run)
