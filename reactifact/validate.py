"""Static validation of agent contracts (§24, §69).

The compile-time counterpart of the reactive runtime: before a run, check that
every consumed artifact type has a producer (or is seeded), that outputs are
either consumed or declared terminal, and that agents are reachable. It catches
the common "I wired two agents but nothing wakes the second" bug without
executing anything.

    report = validate_contracts(agents=AGENTS, seeds=[Question], terminal=[Answer])
    report.raise_for_errors()
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .agent import Agent
from .agent.agent import normalize_units
from .budget import Budget
from .consume import Consume, CorrelatedConsume

Severity = Literal["error", "warning"]


class ValidationError(RuntimeError):
    """Raised by `ValidationReport.raise_for_errors` when an error is present."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(report.render())


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    agents: tuple[str, ...] = ()
    artifact: str = ""


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> ValidationReport:
        if not self.ok:
            raise ValidationError(self)
        return self

    def render(self) -> str:
        if not self.issues:
            return "reactifact validate: ok"
        lines = [
            f"reactifact validate: {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        ]
        for issue in self.issues:
            who = f" [{', '.join(issue.agents)}]" if issue.agents else ""
            lines.append(
                f"{issue.severity.upper():<7} {issue.code}: {issue.message}{who}"
            )
        return "\n".join(lines)


def _name(value: type | str) -> str:
    return value if isinstance(value, str) else value.__name__


def _types_of(specs: Iterable[Consume]) -> set[str]:
    names: set[str] = set()
    for spec in specs:
        if isinstance(spec, CorrelatedConsume):
            names.update(_name(part) for part in spec.require)
        elif spec.artifact_type is not None:
            names.add(_name(spec.artifact_type))
    return names


def _wake_types(agent: Agent) -> set[str]:
    """Types that can wake the agent.

    Explicit `Agent.consumes` wins; otherwise the wake set is derived from the
    steps' reads (the pre-`consumes` behavior).
    """
    if agent.consumes is not None:
        return _types_of(agent.consumes)
    return _types_of(
        consume
        for step in agent.steps or ()
        for consume in (step.wake_specs() or ())
        if consume.wakes
    )


def _read_types(agent: Agent) -> set[str]:
    """Every type the agent's steps read, including read-only inputs.

    Read-only step inputs (`Read(..., required=False)` / `Consume(..., wakes=False)`)
    are not part of the agent's wake contract, but they are still real inputs:
    they must be produced and they keep an artifact from being a dead output.
    """
    return _types_of(
        consume for step in agent.steps or () for consume in (step.wake_specs() or ())
    )


def _produced_types(agent: Agent) -> set[str]:
    names: set[str] = set()
    for step in agent.steps or ():
        for part in step.produces or ():
            names.add(_name(part))
        if step.artifact_type is not None:
            names.add(_name(step.artifact_type))
        names.update(_name(extra) for extra in (step.produces or ())[1:])
    return names


def _cycle_types(
    producers: dict[str, set[str]], consumers: dict[str, set[str]]
) -> list[tuple[str, str]]:
    edges: dict[str, set[str]] = {name: set() for name in producers}
    for produced, agents in producers.items():
        for agent_name in agents:
            edges.setdefault(produced, set()).update(consumers.get(agent_name, set()))
    cycles: list[tuple[str, str]] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            cycles.append((path[-1] if path else node, node))
            return
        visiting.add(node)
        path.append(node)
        for target in edges.get(node, ()):
            visit(target, path)
        path.pop()
        visiting.discard(node)
        done.add(node)

    for node in edges:
        visit(node, [])
    return cycles


def validate_contracts(
    *,
    steps: Iterable[Any] | None = None,
    agents: Iterable[Any] | None = None,
    seeds: Iterable[type | str] = (),
    terminal: Iterable[type | str] = (),
    budget: Budget | None = None,
) -> ValidationReport:
    """Checks artifact-contract reachability without running anything.

    Mirrors `Runtime`: `steps=` accepts raw `Step`s (each is wrapped in an
    implicit agent of one) and `agents=` accepts `Agent`s. Both steps and
    agents can be passed together, steps first.

    - consumed type with no producer and not in `seeds` → **error**
      `unknown_input`;
    - produced type with no consumer and not in `terminal` → **warning**
      `dead_output`;
    - no triggers/consumes → **warning** `never_scheduled`;
    - group whose inputs are never available from seeds/producers → **warning**
      `unreachable`;
    - artifact-type cycle with no bounding Budget → **warning** `cycle`;
    - one artifact type with several producing agents → **warning**
      `ambiguous_producer` (suppressed when they are distinct cases of one
      `Route`).
    """
    agent_list = normalize_units(
        steps=list(steps) if steps is not None else None,
        agents=list(agents or ()),
    )
    seed_names = {_name(type_) for type_ in seeds}
    terminal_names = {_name(type_) for type_ in terminal}
    report = ValidationReport()

    producers: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = {}
    wake_types: dict[str, set[str]] = {}
    seen_names: dict[str, str] = {}
    for agent in agent_list:
        if agent.name in seen_names:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_agent",
                    f"group name {agent.name!r} is used more than once",
                    (agent.name,),
                )
            )
        seen_names[agent.name] = agent.name
        wakes = _wake_types(agent)
        consumed = wakes | _read_types(agent)
        produced = _produced_types(agent)
        wake_types[agent.name] = wakes
        consumers[agent.name] = consumed
        for artifact in produced:
            producers.setdefault(artifact, set()).add(agent.name)
        if not wakes and not agent.triggers:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "never_scheduled",
                    f"group {agent.name!r} has no consumes/triggers",
                    (agent.name,),
                )
            )

    by_name = {agent.name: agent for agent in agent_list}
    for artifact, names in sorted(producers.items()):
        if len(names) < 2:
            continue
        markers = [getattr(by_name[name], "route_case", None) for name in names]
        if all(marker is not None for marker in markers):
            routes = {getattr(marker, "route", None) for marker in markers}
            cases = [getattr(marker, "case", None) for marker in markers]
            if (
                None not in routes
                and len(routes) == 1
                and None not in cases
                and len(cases) == len(set(cases))
            ):
                continue
        report.issues.append(
            ValidationIssue(
                "warning",
                "ambiguous_producer",
                f"artifact type {artifact!r} is produced by {len(names)} agents: "
                f"{', '.join(sorted(names))}; check exclusivity or declare a Route",
                tuple(sorted(names)),
                artifact,
            )
        )

    all_consumed = set().union(*consumers.values()) if consumers else set()
    all_produced = set(producers)
    for artifact in sorted(all_consumed - all_produced - seed_names):
        producers_of_it = sorted(
            agent for agent, types_ in consumers.items() if artifact in types_
        )
        report.issues.append(
            ValidationIssue(
                "error",
                "unknown_input",
                f"artifact type {artifact!r} is consumed but never produced "
                "(add a producer or pass seeds=[...])",
                tuple(producers_of_it),
                artifact,
            )
        )
    for artifact in sorted(all_produced - all_consumed - terminal_names):
        report.issues.append(
            ValidationIssue(
                "warning",
                "dead_output",
                f"artifact type {artifact!r} is produced but never consumed "
                "(declare it in terminal= if it is an output)",
                tuple(sorted(producers[artifact])),
                artifact,
            )
        )

    available = set(seed_names)
    remaining = [agent for agent in agent_list if wake_types.get(agent.name)]
    progressed = True
    while progressed:
        progressed = False
        for agent in list(remaining):
            if wake_types[agent.name] & available:
                available.update(_produced_types(agent))
                remaining.remove(agent)
                progressed = True
    for agent in remaining:
        report.issues.append(
            ValidationIssue(
                "warning",
                "unreachable",
                f"agent {agent.name!r} cannot be reached from the declared seeds",
                (agent.name,),
            )
        )

    if budget is None or not any(
        value is not None
        for value in (
            budget.max_iterations,
            budget.max_runs,
            budget.max_seconds,
            budget.max_tokens,
            budget.max_cost,
        )
    ):
        for source, target in _cycle_types(producers, consumers):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "cycle",
                    f"artifact cycle {source!r} -> {target!r} has no bounding "
                    "Budget (max_iterations/max_runs/max_seconds/...)",
                    (),
                    source,
                )
            )

    return report


__all__ = [
    "ValidationError",
    "ValidationIssue",
    "ValidationReport",
    "validate_contracts",
]
