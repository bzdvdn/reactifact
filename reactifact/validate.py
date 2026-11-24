"""Static validation of agent contracts (§24, §69).

The compile-time counterpart of the reactive runtime: before a run, check that
every consumed artifact type has a producer (or is seeded), that outputs are
either consumed or declared terminal, and that agents are reachable. It catches
the common "I wired two agents but nothing wakes the second" bug without
executing anything.

    report = validate_agents(AGENTS, seeds=[Question], terminal=[Answer])
    report.raise_for_errors()
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .budget import Budget
from .consume import Consume, CorrelatedConsume
from .step import StepGroup
from .step.group import normalize_units

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
    groups: tuple[str, ...] = ()
    artifact: str = ""

    @property
    def agents(self) -> tuple[str, ...]:
        """Deprecated 0.x alias for `groups`."""
        return self.groups


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
            who = f" [{', '.join(issue.groups)}]" if issue.groups else ""
            lines.append(
                f"{issue.severity.upper():<7} {issue.code}: {issue.message}{who}"
            )
        return "\n".join(lines)


def _name(value: type | str) -> str:
    return value if isinstance(value, str) else value.__name__


def _consume_types(agent: StepGroup) -> set[str]:
    specs: Iterable[Consume]
    if agent.consumes is not None:
        specs = agent.consumes
    else:
        specs = [
            consume
            for step in agent.steps or ()
            for consume in (step.consume_specs() or ())
        ]
    names: set[str] = set()
    for spec in specs:
        if isinstance(spec, CorrelatedConsume):
            names.update(_name(part) for part in spec.require)
        elif spec.artifact_type is not None:
            names.add(_name(spec.artifact_type))
    return names


def _produced_types(agent: StepGroup) -> set[str]:
    names: set[str] = set()
    for step in agent.steps or ():
        for part in step.produces or ():
            names.add(_name(part))
        if step.artifact_type is not None:
            names.add(_name(step.artifact_type))
        names.update(_name(extra) for extra in step.also_creates)
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
    groups: Iterable[Any] | None = None,
    seeds: Iterable[type | str] = (),
    terminal: Iterable[type | str] = (),
    budget: Budget | None = None,
) -> ValidationReport:
    """Checks artifact-contract reachability without running anything.

    Mirrors `Runtime`: `steps=` accepts raw `Step`s (each is wrapped in an
    implicit group of one) and `groups=` accepts `StepGroup`s; both can be
    passed together, steps first.

    - consumed type with no producer and not in `seeds` → **error**
      `unknown_input`;
    - produced type with no consumer and not in `terminal` → **warning**
      `dead_output`;
    - no triggers/consumes → **warning** `never_scheduled`;
    - group whose inputs are never available from seeds/producers → **warning**
      `unreachable`;
    - artifact-type cycle with no bounding Budget → **warning** `cycle`.
    """
    agent_list = normalize_units(
        steps=list(steps) if steps is not None else None,
        groups=list(groups) if groups is not None else None,
    )
    seed_names = {_name(type_) for type_ in seeds}
    terminal_names = {_name(type_) for type_ in terminal}
    report = ValidationReport()

    producers: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = {}
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
        consumed = _consume_types(agent)
        produced = _produced_types(agent)
        consumers[agent.name] = consumed
        for artifact in produced:
            producers.setdefault(artifact, set()).add(agent.name)
        if not consumed and not agent.triggers:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "never_scheduled",
                    f"group {agent.name!r} has no consumes/triggers",
                    (agent.name,),
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
    remaining = [agent for agent in agent_list if consumers.get(agent.name)]
    progressed = True
    while progressed:
        progressed = False
        for agent in list(remaining):
            if consumers[agent.name] & available:
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


def validate_agents(
    agents: Iterable[StepGroup],
    *,
    seeds: Iterable[type | str] = (),
    terminal: Iterable[type | str] = (),
    budget: Budget | None = None,
) -> ValidationReport:
    """Deprecated 0.x alias for `validate_contracts(groups=...)`."""
    return validate_contracts(
        groups=list(agents), seeds=seeds, terminal=terminal, budget=budget
    )


__all__ = [
    "ValidationError",
    "ValidationIssue",
    "ValidationReport",
    "validate_agents",
    "validate_contracts",
]
