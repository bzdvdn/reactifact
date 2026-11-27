"""`reactifact check` — the quality loop in one command.

Runs any combination of:

- static contracts (`validate_contracts`) for a `Pipeline` or a step/group module;
- `reactifact.testing` scenarios registered with `@scenario`;
- a dataset evaluation (`reactifact.eval.evaluate`) against a `Pipeline`.

The command is intentionally a thin aggregator: each stage is the same code the
dedicated CLI uses, so `check` adds the gate and the combined report, not a new
runtime concept.

    reactifact check app.pipeline:pipeline
    reactifact check app.pipeline:pipeline --scenarios app.scenarios
    reactifact check app.pipeline:pipeline --evals evals/qa.json --overall 0.8
    reactifact check --contracts app.agents --scenarios app.scenarios
"""

from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Mapping, Sequence
from typing import Any

from ..agent import Agent, Step
from ..budget import Budget
from ..eval import Dataset, core_metrics, evaluate, from_metric
from ..pipeline import Pipeline
from ..testing.record import MODE_ENV_VAR
from ..testing.registry import collect
from ..validate import validate_contracts
from .config import budget_from_config, load_config
from .scenario import ScenarioRun, render_run, run_cases


def _load_object(spec: str) -> Any:
    module_name, has_attr, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"reactifact check: could not import {module_name!r}: {exc}"
        ) from exc
    if has_attr:
        try:
            return getattr(module, attr)
        except AttributeError as exc:
            raise SystemExit(f"reactifact check: {spec!r} not found") from exc
    for candidate in ("pipeline", "PIPELINE", "flow"):
        value = getattr(module, candidate, None)
        if isinstance(value, Pipeline):
            return value
    raise SystemExit(
        f"reactifact check: no Pipeline at {spec!r}; pass an explicit "
        "target like my_app.pipeline:pipeline"
    )


def _units(obj: Any) -> tuple[list[Step[Any]], list[Agent]]:
    """Splits a Pipeline/Step/Agent/list into steps and groups."""
    if isinstance(obj, Pipeline):
        # `Pipeline.agents` already includes implicit groups for raw steps.
        return [], list(obj.agents)
    if isinstance(obj, Step):
        return [obj], []
    if isinstance(obj, Agent):
        return [], [obj]
    if isinstance(obj, (list, tuple)):
        steps = [item for item in obj if isinstance(item, Step)]
        groups = [item for item in obj if isinstance(item, Agent)]
        if len(steps) + len(groups) != len(obj):
            raise SystemExit(
                "reactifact check: a list target may contain only Step/Agent instances"
            )
        return steps, groups
    raise SystemExit(
        "reactifact check: expected a Pipeline, Step, Agent or a list of them, "
        f"got {type(obj).__name__}"
    )


def _seed(pipeline: Pipeline, inputs: Mapping[str, Any]) -> Any:
    seed_type = pipeline.seed_type
    if seed_type is None:
        raise SystemExit(
            "reactifact check: dataset evals need a Pipeline with seed_type=..."
        )
    fields = set(seed_type.model_fields)
    data = dict(inputs)
    if not data:
        return seed_type()
    if set(data) <= fields:
        return seed_type(**data)
    if len(data) == 1 and pipeline.seed_field in fields:
        return seed_type(**{pipeline.seed_field: next(iter(data.values()))})
    raise SystemExit(
        f"reactifact check: dataset inputs {sorted(data)} do not match "
        f"{seed_type.__name__} fields {sorted(fields)}; use keys matching the "
        "model, or a single input key for the pipeline's seed_field"
    )


def _gates(values: Sequence[str]) -> dict[str, float]:
    gates: dict[str, float] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key:
            raise SystemExit(
                f"reactifact check: --gate expects KEY=VALUE, got {value!r}"
            )
        try:
            gates[key] = float(raw)
        except ValueError as exc:
            raise SystemExit(
                f"reactifact check: --gate {key!r} needs a number, got {raw!r}"
            ) from exc
    return gates


def _contracts(
    spec: str,
    *,
    seeds: Sequence[str],
    terminal: Sequence[str],
    budget: Budget | None = None,
) -> bool:
    obj = _load_object(spec)
    steps, groups = _units(obj)
    if not steps and not groups:
        raise SystemExit(f"reactifact check: no steps or groups found at {spec!r}")
    inferred_seeds: list[type | str] = list(seeds)
    inferred_terminal: list[type | str] = list(terminal)
    if isinstance(obj, Pipeline):
        if obj.seed_type is not None and not inferred_seeds:
            inferred_seeds.append(obj.seed_type)
        if obj.answer_type is not None and not inferred_terminal:
            inferred_terminal.append(obj.answer_type)
    report = validate_contracts(
        steps=steps or None,
        groups=groups or None,
        seeds=inferred_seeds,
        terminal=inferred_terminal,
        budget=budget,
    )
    status = "ok" if report.ok else "FAILED"
    print(
        f"contracts  {spec}: {status} ({len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s))"
    )
    for issue in report.errors:
        print(f"  ERROR {issue.code}: {issue.message}")
    return report.ok


def _scenarios(
    modules: Sequence[str],
    *,
    filter_text: str | None,
    mode: str | None,
) -> ScenarioRun | None:
    if mode is not None:
        os.environ[MODE_ENV_VAR] = mode
    cases = collect(list(modules))
    if filter_text:
        cases = [case for case in cases if filter_text in case.name]
    if not cases:
        return None
    return run_cases(cases)


def _evals(
    pipeline: Pipeline,
    dataset_path: str,
    *,
    gates: Mapping[str, float],
    overall: float | None,
) -> bool:
    dataset = Dataset.from_file(dataset_path)
    evaluators = {
        name: from_metric(name, metric) for name, metric in core_metrics.items()
    }

    def target(inputs: Mapping[str, Any]) -> Any:
        return pipeline.run(_seed(pipeline, inputs))

    report = evaluate(dataset, target, evaluators)
    passed = report.passed(gates, overall=overall)
    print(
        f"evals      {dataset_path}: {len(dataset)} case(s), "
        f"overall {report.overall():.3f}"
    )
    aggregate = report.aggregate()
    for key, value in sorted(aggregate.items()):
        print(f"  {key:<24} {value:.3f}")
    if not aggregate:
        print(
            "  (no core metrics measured; name artifacts Answer/Evidence/Claim "
            "or use a custom evaluator suite)"
        )
    if not passed:
        if gates:
            print(f"  gate failed: {dict(gates)}")
        if overall is not None:
            print(f"  gate failed: overall >= {overall:.3f}")
    return passed


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config()
    target_spec = args.target
    contracts_spec = args.contracts or target_spec or config.groups
    scenarios_specs = args.scenarios or config.scenarios
    evals_path = args.evals if args.evals is not None else config.evals
    overall = args.overall if args.overall is not None else config.overall
    gates = args.gate or [f"{key}={value}" for key, value in config.gates.items()]
    if evals_path is not None and target_spec is None:
        raise SystemExit(
            "reactifact check: --evals needs a Pipeline as the positional "
            "target (for seed_type and steps)"
        )
    if (
        target_spec is None
        and contracts_spec is None
        and not scenarios_specs
        and evals_path is None
    ):
        raise SystemExit(
            "reactifact check: pass a target/--contracts, --scenarios and/or --evals"
        )
    if args.no_contracts and not scenarios_specs and evals_path is None:
        raise SystemExit("reactifact check: --no-contracts leaves no stage to run")

    ok = True
    print("reactifact check")
    print()

    # Scenarios first: `collect()` reloads already-imported modules, which
    # replaces their pydantic classes. Load the target afterwards so its
    # types match the reloaded module.
    scenario_run = (
        _scenarios(
            scenarios_specs,
            filter_text=args.filter,
            mode=args.mode,
        )
        if scenarios_specs
        else None
    )

    target_obj = _load_object(target_spec) if target_spec else None
    pipeline = target_obj if isinstance(target_obj, Pipeline) else None

    if not args.no_contracts and contracts_spec is not None:
        ok = (
            _contracts(
                contracts_spec,
                seeds=args.seed or config.seeds,
                terminal=args.terminal or config.terminal,
                budget=budget_from_config(config),
            )
            and ok
        )

    if scenarios_specs:
        if scenario_run is None:
            print("scenarios  no scenarios found (check the module path and -k filter)")
            ok = False
        else:
            print(f"scenarios  {', '.join(scenarios_specs)}")
            render_run(scenario_run, show_state=not args.no_state)
            ok = scenario_run.ok and ok

    if evals_path is not None:
        if pipeline is None:
            raise SystemExit(
                "reactifact check: --evals needs a Pipeline as the positional "
                "target (for seed_type and steps)"
            )
        ok = (
            _evals(
                pipeline,
                evals_path,
                gates=_gates(gates),
                overall=overall,
            )
            and ok
        )

    print()
    print("check: " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_check = sub.add_parser(
        "check",
        help="run contracts, scenarios and dataset evals as one gate",
    )
    p_check.add_argument(
        "target",
        nargs="?",
        default=None,
        help="module:pipeline for contracts and evals (a Pipeline)",
    )
    p_check.add_argument(
        "--contracts",
        default=None,
        metavar="MODULE[:ATTR]",
        help="check a Pipeline/Step/Agent/list instead of the positional target",
    )
    p_check.add_argument(
        "--seed",
        action="append",
        metavar="TYPE",
        help="artifact type supplied externally (repeatable)",
    )
    p_check.add_argument(
        "--terminal",
        action="append",
        metavar="TYPE",
        help="artifact type allowed to have no consumer (repeatable)",
    )
    p_check.add_argument(
        "--scenarios",
        action="append",
        default=[],
        metavar="MODULE",
        help="run scenarios from this module (repeatable)",
    )
    p_check.add_argument(
        "-k",
        "--filter",
        default=None,
        help="only run scenarios whose name contains this substring",
    )
    p_check.add_argument(
        "--mode",
        choices=["live", "record", "replay"],
        default=None,
        help="scenario LLM mode (REACTIFACT_SCENARIO_MODE)",
    )
    p_check.add_argument(
        "--no-state",
        action="store_true",
        help="omit the scenario-state dump from a failure report",
    )
    p_check.add_argument(
        "--evals",
        default=None,
        metavar="DATASET",
        help="dataset file (.json/.jsonl) to evaluate against the Pipeline",
    )
    p_check.add_argument(
        "--gate",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="minimum aggregate for one eval metric (repeatable)",
    )
    p_check.add_argument(
        "--overall",
        type=float,
        default=None,
        help="minimum suite overall score",
    )
    p_check.add_argument(
        "--no-contracts",
        action="store_true",
        help="skip the contract stage",
    )
    p_check.set_defaults(func=cmd_check)


__all__ = ["add_parser"]
