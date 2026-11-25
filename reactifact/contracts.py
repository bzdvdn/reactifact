"""Static contracts for agents: steps, consumes/produces and tools.

This is the OpenAPI-shaped view of a reactifact graph: not a run trace, but the
declared surface a module exposes. It is intentionally read-only and
deterministic — `reactifact contracts <module>` renders Markdown/JSON from the
same `StepGroup` objects the runtime uses, so docs cannot drift from code.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .consume import Consume
from .step import Step, StepGroup


@dataclass(frozen=True)
class ConsumeContract:
    artifact: str
    kind: str = "Consume"
    wakes: bool = True
    condition: str = ""
    debounce: bool = False
    include_subclasses: bool = False


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str = ""
    destructive: bool = False
    timeout: float | None = None
    retry: dict[str, Any] | None = None
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepContract:
    name: str
    kind: str
    consumes: tuple[ConsumeContract, ...] = ()
    produces: tuple[str, ...] = ()
    tools: tuple[ToolContract, ...] = ()
    priority: int = 0
    timeout: float | None = None
    retry: dict[str, Any] | None = None


@dataclass(frozen=True)
class GroupContract:
    name: str
    kind: str
    consumes: tuple[ConsumeContract, ...] = ()
    steps: tuple[StepContract, ...] = ()


@dataclass(frozen=True)
class ContractReport:
    """The declared contract of one or more `StepGroup`s."""

    groups: tuple[GroupContract, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"groups": [_group_dict(group) for group in self.groups]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = ["# reactifact contracts", ""]
        if not self.groups:
            lines.append("_No groups._")
            return "\n".join(lines) + "\n"
        for group in self.groups:
            lines.extend(
                [
                    f"## `{group.name}` ({group.kind})",
                    "",
                ]
            )
            if group.consumes:
                lines.append(f"- consumes: {_join_consumes(group.consumes)}")
            for step in group.steps:
                lines.append(f"### `{step.name}` ({step.kind})")
                if step.consumes:
                    lines.append(f"- consumes: {_join_consumes(step.consumes)}")
                if step.produces:
                    lines.append(
                        "- produces: "
                        + ", ".join(f"`{name}`" for name in step.produces)
                    )
                if step.tools:
                    lines.append("- tools:")
                    for tool in step.tools:
                        flags = " (destructive)" if tool.destructive else ""
                        desc = f" — {tool.description}" if tool.description else ""
                        lines.append(f"  - `{tool.name}`{flags}{desc}")
                if step.timeout is not None:
                    lines.append(f"- timeout: {step.timeout:g}s")
                if step.retry is not None:
                    lines.append(
                        "- retry: "
                        + ", ".join(f"{k}={v}" for k, v in step.retry.items())
                    )
                lines.append("")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _type_name(value: Any) -> str:
    return getattr(value, "__name__", str(value))


def _retry_dict(policy: Any) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "max_attempts": getattr(policy, "max_attempts", None),
        "backoff": getattr(policy, "backoff", None),
        "max_backoff": getattr(policy, "max_backoff", None),
    }


def _consume_contract(value: Any) -> ConsumeContract:
    if isinstance(value, Consume):
        condition = value.condition
        return ConsumeContract(
            artifact=_type_name(value.artifact_type),
            kind=type(value).__name__,
            wakes=value.wakes,
            condition=getattr(
                condition, "__name__", repr(condition) if condition else ""
            ),
            debounce=value.debounce,
            include_subclasses=value.include_subclasses,
        )
    return ConsumeContract(artifact=_type_name(value), kind="ArtifactType")


def _consumes(values: Any) -> tuple[ConsumeContract, ...]:
    return tuple(_consume_contract(value) for value in (values or ()))


def _tool_contracts(tools: Any) -> tuple[ToolContract, ...]:
    values = tools.values() if isinstance(tools, dict) else (tools or ())
    result: list[ToolContract] = []
    for tool in values:
        result.append(
            ToolContract(
                name=getattr(tool, "name", type(tool).__name__),
                description=getattr(tool, "description", "") or "",
                destructive=bool(getattr(tool, "destructive", False)),
                timeout=getattr(tool, "timeout", None),
                retry=_retry_dict(getattr(tool, "retry", None)),
                schema=dict(getattr(tool, "schema", {}) or {}),
            )
        )
    return tuple(result)


def _step_contract(step: Step[Any]) -> StepContract:
    produces = getattr(step, "produces", None) or ()
    return StepContract(
        name=getattr(step, "name", "") or type(step).__name__,
        kind=type(step).__name__,
        consumes=_consumes(getattr(step, "consumes", None)),
        produces=tuple(_type_name(item) for item in produces),
        tools=_tool_contracts(getattr(step, "tools", None)),
        priority=int(getattr(step, "priority", 0) or 0),
        timeout=getattr(step, "timeout", None),
        retry=_retry_dict(getattr(step, "retry", None)),
    )


def _group_contract(group: StepGroup) -> GroupContract:
    return GroupContract(
        name=getattr(group, "name", "") or type(group).__name__,
        kind=type(group).__name__,
        consumes=_consumes(getattr(group, "consumes", None)),
        steps=tuple(
            _step_contract(step) for step in (getattr(group, "steps", None) or ())
        ),
    )


def describe_contracts(groups: Sequence[StepGroup]) -> ContractReport:
    """Describe the declared surface of `groups` (no run, no LLM)."""
    return ContractReport(groups=tuple(_group_contract(group) for group in groups))


def _join_consumes(consumes: Sequence[ConsumeContract]) -> str:
    parts: list[str] = []
    for consume in consumes:
        suffix = "" if consume.wakes else " (read-only)"
        if consume.condition:
            suffix += f" when {consume.condition}"
        parts.append(f"`{consume.artifact}`{suffix}")
    return ", ".join(parts)


def _group_dict(group: GroupContract) -> dict[str, Any]:
    return {
        "name": group.name,
        "kind": group.kind,
        "consumes": [_consume_dict(item) for item in group.consumes],
        "steps": [
            {
                "name": step.name,
                "kind": step.kind,
                "consumes": [_consume_dict(item) for item in step.consumes],
                "produces": list(step.produces),
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "destructive": tool.destructive,
                        "timeout": tool.timeout,
                        "retry": tool.retry,
                        "schema": tool.schema,
                    }
                    for tool in step.tools
                ],
                "priority": step.priority,
                "timeout": step.timeout,
                "retry": step.retry,
            }
            for step in group.steps
        ],
    }


def _consume_dict(consume: ConsumeContract) -> dict[str, Any]:
    return {
        "artifact": consume.artifact,
        "kind": consume.kind,
        "wakes": consume.wakes,
        "condition": consume.condition,
        "debounce": consume.debounce,
        "include_subclasses": consume.include_subclasses,
    }


__all__ = [
    "ConsumeContract",
    "ContractReport",
    "GroupContract",
    "StepContract",
    "ToolContract",
    "describe_contracts",
]
