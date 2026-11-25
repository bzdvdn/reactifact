"""`reactifact run` — run a `Pipeline` with one seed artifact."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..pipeline import Pipeline
from .common import DEFAULT_TRACE_PATH, load_attr, load_pipeline


def _load_model(spec: str) -> type[BaseModel]:
    value = load_attr(spec)
    if not (isinstance(value, type) and issubclass(value, BaseModel)):
        raise SystemExit(f"{spec!r} must resolve to a pydantic model")
    return value


def _configure_trace(
    pipeline: Pipeline, args: argparse.Namespace
) -> tuple[Pipeline, str | None, Any | None]:
    """Returns a CLI copy of the pipeline, the trace path and a store to close."""
    pipeline = copy.copy(pipeline)
    if args.no_trace:
        pipeline.tracer = None
        pipeline.trace_store = None
        return pipeline, None, None
    if pipeline.trace_store is not None:
        return pipeline, None, None

    from ..tracing import Tracer, TraceStore

    path = args.trace or DEFAULT_TRACE_PATH
    store = TraceStore(path)
    pipeline.trace_store = store
    pipeline.tracer = Tracer(store=store)
    return pipeline, path, store


def _announce_trace(path: str | None, run_id: str) -> None:
    if not run_id:
        return
    if path:
        print(f"trace: {path} ({run_id})", file=sys.stderr)
        print(
            "open:  reactifact trace"
            + ("" if path == DEFAULT_TRACE_PATH else f" {path}"),
            file=sys.stderr,
        )
    else:
        print(f"trace: run {run_id}", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    pipeline, trace_path, created_store = _configure_trace(
        load_pipeline(args.pipeline), args
    )

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

    try:
        result = pipeline.run(seed)
        _announce_trace(trace_path, result.trace_id)
        show = _load_model(args.show) if args.show else pipeline.answer_type
        if show is not None:
            artifact = result.latest(show)
            if artifact is None:
                print(f"no {show.__name__} artifact produced", file=sys.stderr)
                return 1
            print(
                json.dumps(
                    artifact.data.model_dump(mode="json"),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0

        print(
            f"runs: {result.runs}  outcome: {result.outcome.value}  hash: {result.hash}"
        )
        for artifact in result.context.list_artifacts():
            print(f"  {type(artifact.data).__name__:<24} {artifact.id}")
        return 0
    finally:
        if created_store is not None:
            created_store.close()


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
    trace = p_run.add_mutually_exclusive_group()
    trace.add_argument(
        "--trace",
        nargs="?",
        const=DEFAULT_TRACE_PATH,
        default=DEFAULT_TRACE_PATH,
        metavar="PATH",
        help=f"record a trace here (default: {DEFAULT_TRACE_PATH})",
    )
    trace.add_argument(
        "--no-trace",
        action="store_true",
        help="do not record a trace for this run",
    )
    p_run.set_defaults(func=cmd_run)
