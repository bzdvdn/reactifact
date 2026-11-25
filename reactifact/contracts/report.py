"""Static contracts for agents: steps, consumes/produces and tools.

This is the OpenAPI-shaped view of a reactifact graph: not a run trace, but the
declared surface a module exposes. It is intentionally read-only and
deterministic — `reactifact contracts <module>` renders Markdown/JSON from the
same `StepGroup` objects the runtime uses, so docs cannot drift from code.
"""

from __future__ import annotations

import base64
import html as _html
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..consume import Consume
from ..step import Step, StepGroup


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
    produces: tuple[str, ...] = ()
    steps: tuple[StepContract, ...] = ()


@dataclass(frozen=True)
class ContractReport:
    """The declared contract of one or more `StepGroup`s.

    `seed_schema` / `answer_schema` are optional JSON Schemas attached by
    `Pipeline.describe()`; the group-only `describe_contracts()` leaves them
    `None`, so existing callers and pages are unchanged.
    """

    groups: tuple[GroupContract, ...] = ()
    seed_schema: dict[str, Any] | None = None
    answer_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "groups": [_group_dict(group) for group in self.groups]
        }
        if self.seed_schema is not None:
            payload["seed"] = self.seed_schema
        if self.answer_schema is not None:
            payload["answer"] = self.answer_schema
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_mermaid(
        self,
        *,
        max_nodes: int | None = None,
        max_edges: int | None = None,
    ) -> str:
        """A group/artifact connection graph (static, no run state)."""
        lines = ["flowchart LR"]
        artifact_ids: dict[str, str] = {}
        edges = 0
        truncated = False

        def at_node_limit() -> bool:
            return (
                max_nodes is not None and len(artifact_ids) + groups_seen >= max_nodes
            )

        groups_seen = 0
        for index, group in enumerate(self.groups):
            if at_node_limit():
                truncated = True
                break
            group_id = f"G{index}"
            lines.append(f'    {group_id}["{_mermaid_escape(group.name)}"]')
            groups_seen += 1
            consumes = list(
                dict.fromkeys(
                    item.artifact
                    for item in (
                        *group.consumes,
                        *(item for step in group.steps for item in step.consumes),
                    )
                )
            )
            for name in consumes:
                is_new = name not in artifact_ids
                if is_new and at_node_limit():
                    truncated = True
                    continue
                artifact_id = _artifact_node_id(lines, artifact_ids, name)
                if max_edges is not None and edges >= max_edges:
                    truncated = True
                    continue
                lines.append(f"    {artifact_id} -.->|consumes| {group_id}")
                edges += 1
            for name in group.produces:
                is_new = name not in artifact_ids
                if is_new and at_node_limit():
                    truncated = True
                    continue
                artifact_id = _artifact_node_id(lines, artifact_ids, name)
                if max_edges is not None and edges >= max_edges:
                    truncated = True
                    continue
                lines.append(f"    {group_id} ==>|produces| {artifact_id}")
                edges += 1
        if truncated:
            lines.append('    TRUNC["… graph truncated …"]')
        return "\n".join(lines)

    def to_html(self, *, title: str = "reactifact contracts") -> str:
        """A self-contained HTML page (no external CSS/JS, dark-mode aware)."""
        groups = "".join(_group_html(group) for group in self.groups)
        if not groups:
            groups = '<p class="empty">No groups.</p>'
        template_dir = Path(__file__).resolve().parent / "templates"
        template = (template_dir / "contracts.html").read_text(encoding="utf-8")
        favicon = base64.b64encode((template_dir / "favicon.svg").read_bytes()).decode(
            "ascii"
        )
        steps = sum(len(group.steps) for group in self.groups)
        tools = sum(len(step.tools) for group in self.groups for step in group.steps)
        summary = (
            f"<span><b>{len(self.groups)}</b> groups</span>"
            f"<span><b>{steps}</b> steps</span>"
            f"<span><b>{tools}</b> tools</span>"
        )
        return (
            template.replace("__TITLE__", _html.escape(title))
            .replace("__FAVICON__", favicon)
            .replace("__SUMMARY__", summary)
            .replace(
                "__GRAPH__",
                _html.escape(self.to_mermaid(max_nodes=160, max_edges=320)),
            )
            .replace("__GROUPS__", groups)
        )

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
    steps = tuple(
        _step_contract(step) for step in (getattr(group, "steps", None) or ())
    )
    produces = tuple(dict.fromkeys(name for step in steps for name in step.produces))
    return GroupContract(
        name=getattr(group, "name", "") or type(group).__name__,
        kind=type(group).__name__,
        consumes=_consumes(getattr(group, "consumes", None)),
        produces=produces,
        steps=steps,
    )


def describe_contracts(groups: Sequence[StepGroup]) -> ContractReport:
    """Describe the declared surface of `groups` (no run, no LLM)."""
    return ContractReport(groups=tuple(_group_contract(group) for group in groups))


def _mermaid_escape(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace('"', "'")


def _artifact_node_id(lines: list[str], ids: dict[str, str], name: str) -> str:
    node = ids.get(name)
    if node is None:
        node = f"A{len(ids)}"
        ids[name] = node
        lines.append(f'    {node}["{_mermaid_escape(name)}"]')
    return node


def _chips(values: Sequence[str]) -> str:
    return "".join(
        f'<span class="chip">{_html.escape(value)}</span>' for value in values
    )


def _slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return out or "item"


def _search_attr(*parts: str) -> str:
    return _html.escape(" ".join(part for part in parts if part), quote=True)


def _io_block(label: str, values: Sequence[str]) -> str:
    if not values:
        return ""
    return (
        f'<div><div class="io-label">{_html.escape(label)}</div>'
        f'<div class="chips">{_chips(values)}</div></div>'
    )


def _group_html(group: GroupContract) -> str:
    group_id = f"group-{_slug(group.name)}"
    steps = "".join(_step_html(step, group_id) for step in group.steps)
    if not steps:
        steps = '<p class="empty">No steps.</p>'
    consumes = [*group.consumes]
    seen = {item.artifact for item in consumes}
    for step in group.steps:
        for item in step.consumes:
            if item.artifact not in seen:
                seen.add(item.artifact)
                consumes.append(item)
    io = ""
    if consumes or group.produces:
        io = (
            '<div class="tree-io">'
            + _io_block("consumes", [item.artifact for item in consumes])
            + _io_block("produces", list(group.produces))
            + "</div>"
        )
    search = _search_attr(
        group.name,
        group.kind,
        *(item.artifact for item in consumes),
        *group.produces,
        *(step.name for step in group.steps),
        *(step.kind for step in group.steps),
        *(tool.name for step in group.steps for tool in step.tools),
    )
    return (
        f'<details class="group tree-group" id="{group_id}" open data-search="{search}">'
        '<summary class="tree-row group-row">'
        f'<span class="tree-name"><code>{_html.escape(group.name)}</code></span>'
        f'<a class="anchor" href="#{group_id}" onclick="event.stopPropagation()" title="link to group">#</a>'
        f'<span class="badge accent">{_html.escape(group.kind)}</span>'
        '<span class="spacer"></span>'
        f'<span class="count">{len(group.steps)} step(s)</span>'
        "</summary>"
        f'<div class="tree-children">{io}{steps}</div>'
        "</details>"
    )


def _step_html(step: StepContract, group_id: str = "group") -> str:
    consumes = [
        item.artifact + ("" if item.wakes else " · read-only") for item in step.consumes
    ]
    meta: list[str] = []
    if step.timeout is not None:
        meta.append(f"timeout {step.timeout:g}s")
    if step.priority:
        meta.append(f"priority {step.priority}")
    if step.retry is not None:
        meta.append("retry " + ", ".join(f"{k}={v}" for k, v in step.retry.items()))
    meta_html = (
        '<div class="tree-meta">'
        + "".join(f'<span class="pill">{_html.escape(item)}</span>' for item in meta)
        + "</div>"
        if meta
        else ""
    )
    io = ""
    if consumes or step.produces:
        io = (
            '<div class="tree-io">'
            + _io_block("consumes", consumes)
            + _io_block("produces", list(step.produces))
            + "</div>"
        )
    tools = ""
    if step.tools:
        items = []
        for tool in step.tools:
            flags = (
                '<span class="badge accent">destructive</span>'
                if tool.destructive
                else ""
            )
            desc = (
                f'<span class="tool-desc">{_html.escape(tool.description)}</span>'
                if tool.description
                else ""
            )
            schema = ""
            if tool.schema:
                schema = (
                    '<details class="tree-schema"><summary>schema</summary><pre>'
                    + _html.escape(
                        json.dumps(tool.schema, ensure_ascii=False, indent=2)
                    )
                    + "</pre></details>"
                )
            items.append(
                '<div class="tree-tool">'
                f"<code>{_html.escape(tool.name)}</code>{flags}{desc}{schema}</div>"
            )
        tools = (
            '<div class="tree-tools"><div class="io-label">tools</div>'
            + "".join(items)
            + "</div>"
        )
    search = _search_attr(
        step.name,
        step.kind,
        *consumes,
        *step.produces,
        *(tool.name for tool in step.tools),
        *(tool.description for tool in step.tools),
    )
    step_id = f"{group_id}-step-{_slug(step.name)}"
    return (
        f'<details class="step tree-step" id="{step_id}" open data-search="{search}">'
        '<summary class="tree-row step-row">'
        f'<span class="tree-name"><code>{_html.escape(step.name)}</code></span>'
        f'<a class="anchor" href="#{step_id}" onclick="event.stopPropagation()" title="link to step">#</a>'
        f'<span class="kind">{_html.escape(step.kind)}</span>'
        '<span class="spacer"></span>'
        + (
            f'<span class="count">{len(step.tools)} tool(s)</span>'
            if step.tools
            else ""
        )
        + "</summary>"
        f'<div class="tree-children">{meta_html}{io}{tools}</div>'
        "</details>"
    )


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
        "produces": list(group.produces),
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
