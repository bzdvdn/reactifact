"""reactifact.viz — Mermaid diagram renderers (§72).

Pure functions returning Mermaid source strings (no rendering dependency).
Render them anywhere Mermaid works (GitHub, Notion, mermaid.live), or view the
trace diagram directly in the dashboard and the CLI:

    python -m reactifact graph examples.knowledge.agents
    python -m reactifact context <sessions-db>
    python -m reactifact trace traces.db [run_id]

The two "graphs" here are honest to the architecture: `blueprint` is the
*static map* (what agents can consume/produce), `context_to_mermaid` is the
*dynamic state* — artifacts and their provenance relations (§72). There is no
execution graph to draw; the runtime derives execution from state changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .agents import Agent
from .context import Context

if TYPE_CHECKING:
    from .tracing.models import RunTrace


def _esc(text: Any) -> str:
    """Mermaid-safe inline label text (quotes/newlines are the danger zone)."""
    return str(text).replace("\r", " ").replace("\n", " ").replace('"', "'").strip()


def _artifact_node(lines: list[str], node_ids: dict[str, str], type_name: str) -> str:
    """Returns (and registers) the mermaid node id for an artifact type."""
    node = node_ids.get(type_name)
    if node is None:
        node = f"ART{len(node_ids)}"
        node_ids[type_name] = node
        lines.append(f'    {node}["{_esc(type_name)}"]')
    return node


def blueprint(
    agents: Sequence[Agent],
    *,
    with_stages: bool = True,
    title: str = "reactifact blueprint",
) -> str:
    """Static map of the system: artifact types = nodes, agents = edges.

    For every `Agent` renders its `consumes` (input edge) and `produces`
    (create edge; a `lifecycle` edge for `StatusMachine`-style produces).
    """
    lines: list[str] = ["flowchart LR"]
    lines.append(f'    subgraph SG["{_esc(title)}"]')
    lines.append("        direction LR")
    node_ids: dict[str, str] = {}
    seen: set[tuple[str, str, str]] = set()
    for i, agent in enumerate(agents):
        aid = f"A{i}"
        produces = [
            p
            for p in agent.steps or ()
            if getattr(p, "artifact_type", None) is not None
        ]
        agent_label = agent.name or f"agent{i}"
        if with_stages and produces:
            agent_label += "<br/>" + " · ".join(type(p).__name__ for p in produces)
        lines.append(f'        {aid}["{_esc(agent_label)}"]')

        for consume in agent.consumes or ():
            tname = getattr(consume, "artifact_type", None)
            if tname is None:
                continue
            node = _artifact_node(lines, node_ids, tname.__name__)
            key = ("consume", node, aid)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"        {node} -.->|Consume| {aid}")

        for produce in produces:
            ptype = produce.artifact_type
            if ptype is None:
                continue
            node = _artifact_node(
                lines, node_ids, getattr(ptype, "__name__", type(ptype).__name__)
            )
            machine = hasattr(produce, "next_status") and hasattr(
                produce, "status_field"
            )
            action = "lifecycle" if machine else "creates"
            key = ("produce", aid, node)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"        {aid} ==>|{action}| {node}")

    lines.append("    end")
    return "\n".join(lines)


def context_to_mermaid(
    context: Context,
    *,
    relate: bool = True,
    limit: int | None = None,
) -> str:
    """Live provenance graph of a context: artifacts grouped by type + relations.

    The "graph" here is the *state* — artifacts and `patch.link` relations, not
    orchestration (§72). With `limit` only the first N artifacts (in store order)
    are shown and relations are restricted to them.
    """
    artifacts = context.list_artifacts()
    if limit is not None and limit > 0:
        artifacts = artifacts[:limit]

    type_ids: dict[str, str] = {}
    node_ids: dict[str, str] = {}
    for i, artifact in enumerate(artifacts):
        tname = artifact.data.__class__.__name__
        type_ids.setdefault(tname, f"T{len(type_ids)}")
        node_ids[artifact.id] = f"N{i}"

    lines: list[str] = ["flowchart TD"]
    by_type: dict[str, list[str]] = {}
    for artifact in artifacts:
        by_type.setdefault(artifact.data.__class__.__name__, []).append(artifact.id)
    for tname, tid in type_ids.items():
        lines.append(f'    subgraph {tid}["{_esc(tname)}"]')
        for mid in by_type[tname]:
            lines.append(f'        {node_ids[mid]}["{_esc(tname)}:{_esc(mid)}"]')
        lines.append("    end")

    if relate:
        for rel in context.relations():
            if rel.source_id not in node_ids or rel.target_id not in node_ids:
                continue
            lines.append(
                f'    {node_ids[rel.source_id]} -->|"{_esc(rel.relation)}"| '
                f"{node_ids[rel.target_id]}"
            )
    return "\n".join(lines)


def trace_to_mermaid(trace: RunTrace) -> str:
    """sequenceDiagram of one run: agent spans over time, writes, LLM calls."""
    lines: list[str] = ["sequenceDiagram"]
    lines.append("    participant RT as Runtime")
    pid_of: dict[str, str] = {"__runtime__": "RT"}
    has_llm = any(span.llm_calls for span in trace.spans)
    for span in trace.spans:
        if span.agent not in pid_of:
            pid = f"A{len(pid_of)}"
            pid_of[span.agent] = pid
            lines.append(f'    participant {pid} as "{_esc(span.agent)}"')
    if has_llm:
        lines.append('    participant LL as "LLM (recording)"')

    for span in trace.spans:
        pid = pid_of[span.agent]
        event = _esc(span.event_type) if span.event_type else "react"
        if span.event_artifact_type:
            event += f" · {_esc(span.event_artifact_type)}"
        lines.append(f"    RT->>{pid}: {event}")
        lines.append(f"    activate {pid}")
        for call in span.llm_calls:
            model = call.model or call.provider or "llm"
            stat = (
                f"{call.prompt_tokens} in → {call.completion_tokens} out · "
                f"{call.latency_ms:.0f} ms"
            )
            span_msg = f"{_esc(model)} · {stat}"
            if call.error:
                span_msg += f" · ⚠ {_esc(call.error)}"
            lines.append(f"    {pid}->>LL: {span_msg}")
        for tool in span.tools:
            msg = f"{tool.tool} · {tool.latency_ms:.0f} ms"
            if not tool.ok:
                msg += f" · ⚠ {_esc(tool.error or 'failed')}"
            lines.append(f"    {pid}->>{pid}: tool {_esc(msg)}")
        result = [
            _plural(len(span.writes), "write"),
            _plural(len(span.reads), "read"),
            f"{span.latency_ms:.0f} ms",
        ]
        if span.error:
            result.append(f"⚠ {_esc(span.error)}")
        lines.append(f"    {pid}-->>RT: {' · '.join(result)}")
        lines.append(f"    deactivate {pid}")

    outcome = trace.outcome or "?"
    lines.append(
        f"    Note over RT: outcome={_esc(outcome)} · "
        f"duration={trace.duration_ms:.0f} ms"
    )
    return "\n".join(lines)


def trace_provenance_to_mermaid(trace: RunTrace) -> str:
    """Mermaid provenance graph of one run: written artifacts + `patch.link` edges (§34).

    Artifacts written by any span become nodes; the `relations` recorded by the
    spans become the edges — a per-run "evidence graph" (`Answer →supported_by→
    Claim →derived_from→ Evidence →extracted_from→ Doc`).
    """

    def _short(data_type: str) -> str:
        return data_type.rsplit(".", 1)[-1] if data_type else "artifact"

    nodes: dict[str, tuple[str, str]] = {}
    for span in trace.spans:
        for write in span.writes:
            nodes.setdefault(
                write.artifact_id, (_short(write.data_type), write.artifact_id)
            )
        for rel in span.relations:
            nodes.setdefault(
                rel.source_id, (rel.source_type or "artifact", rel.source_id)
            )
            nodes.setdefault(
                rel.target_id, (rel.target_type or "artifact", rel.target_id)
            )

    for span in trace.spans:
        for rel in span.relations:
            if rel.source_id not in nodes:
                nodes[rel.source_id] = (rel.source_type or "artifact", rel.source_id)
            if rel.target_id not in nodes:
                nodes[rel.target_id] = (rel.target_type or "artifact", rel.target_id)

    if not nodes:
        return 'flowchart TD\n    EMPTY["no provenance recorded"]'

    lines: list[str] = ["flowchart TD"]
    node_ids: dict[str, str] = {}
    for index, (artifact_id, (tname, _)) in enumerate(nodes.items()):
        node = f"N{index}"
        node_ids[artifact_id] = node
        lines.append(f'    {node}["{_esc(tname)}:{_esc(artifact_id)}"]')
    for span in trace.spans:
        for rel in span.relations:
            if rel.source_id in node_ids and rel.target_id in node_ids:
                lines.append(
                    f'    {node_ids[rel.source_id]} -->|"{_esc(rel.relation)}"| '
                    f"{node_ids[rel.target_id]}"
                )
    return "\n".join(lines)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("s" if n != 1 else "")


__all__ = [
    "blueprint",
    "context_to_mermaid",
    "trace_provenance_to_mermaid",
    "trace_to_mermaid",
]
