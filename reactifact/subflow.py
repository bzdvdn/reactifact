"""Subflows: run a child agent on an isolated Context with provenance (§39)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .agent import Agent
from .artifacts import Artifact
from .budget import Budget, RunStats
from .context import Context
from .runtime import Runtime


@dataclass
class SubflowResult:
    """Outcome of `run_subflow`: the child state, its outputs and trace link."""

    context: Context
    seed: Artifact[Any] | None
    outputs: list[Artifact[Any]] = field(default_factory=list)
    output: Artifact[Any] | None = None
    run_id: str = ""
    parent_run_id: str = ""
    stats: RunStats | None = None


async def run_subflow(
    parent: Context,
    *,
    agents: Agent | Sequence[Agent] | None = None,
    groups: Agent | Sequence[Agent] | None = None,
    input: BaseModel | Artifact[Any],
    output_type: type[BaseModel] | None = None,
    isolate: bool = True,
    link: str | None = None,
    source: Artifact[Any] | str | None = None,
    inherit_trace: bool = True,
    inherit_budget: bool = True,
    budget: Budget | None = None,
    max_iterations: int = 100,
    name: str = "subflow",
) -> SubflowResult:
    """Runs `agents` on a child Context and returns its state and outputs.

    `agents=` is one `Agent` or a sequence; `groups=` is a compatibility alias.

    `isolate=True` starts from an empty Context (just the seeded input);
    `isolate=False` starts from a `parent.branch()` so the child sees the
    parent's artifacts, still without mutating the parent.

    `link=` adds a provenance edge from each output to `source` (default: the
    seeded input) inside the child Context. `inherit_trace` passes the parent's
    tracer and stamps the child run's `trace_metadata.parent_run_id`;
    `inherit_budget` reuses the parent's `Budget` (the shared resources mean
    the parent's `BudgetLLM` wrapper still counts the child's usage — do not
    add it twice).
    """
    if agents is not None and groups is not None:
        raise TypeError("Pass only one of agents= or groups=")
    resolved = agents if agents is not None else groups
    if resolved is None:
        raise TypeError("run_subflow requires agents=")
    group_list = [resolved] if isinstance(resolved, Agent) else list(resolved)
    parent_versions = {
        artifact.id: artifact.version for artifact in parent.list_artifacts()
    }

    child = Context(resources=parent.resources) if isolate else parent.branch(name=name)

    if isinstance(input, Artifact):
        seed = child.get(input.id)
        seed_existed = seed is not None
        if seed is None:
            seed = child.create(input.data, id=input.id)
    else:
        seed_existed = False
        seed = child.create(input)
    if seed_existed:
        # A branch already contains the parent artifact, so no create() event
        # exists; wake the child agents explicitly from the seed.
        child.touch(seed.id)

    source_id: str | None = None
    if link is not None:
        if source is None:
            source_id = seed.id
        elif isinstance(source, str):
            source_id = source
        else:
            source_id = source.id
            if child.get(source_id) is None:
                child.create(source.data, id=source_id)

    child_budget = budget
    if child_budget is None and inherit_budget:
        child_budget = parent.resources.budget
    parent_tracer = parent.resources.tracer if inherit_trace else None
    child_runtime = Runtime(
        child,
        groups=group_list,
        budget=child_budget,
        tracer=parent_tracer,
        trace_scope=parent.resources.trace_scope,
    )

    request: dict[str, Any] | None = None
    if parent.resources.run_id:
        request = {
            "trace_metadata": {
                "parent_run_id": parent.resources.run_id,
                "subflow": name,
            }
        }
        if parent.resources.trace_scope:
            request["workspace_id"] = parent.resources.trace_scope

    await child_runtime.arun(max_iterations=max_iterations, request=request)

    candidates = (
        child.list_artifacts(output_type)
        if output_type is not None
        else child.list_artifacts()
    )
    outputs = [
        artifact
        for artifact in candidates
        if artifact.id != seed.id
        and parent_versions.get(artifact.id, -1) < artifact.version
    ]
    if link is not None and source_id is not None:
        for artifact in outputs:
            if artifact.id != source_id:
                child.link(artifact.id, link, source_id)

    return SubflowResult(
        context=child,
        seed=seed,
        outputs=outputs,
        output=outputs[-1] if outputs else None,
        run_id=child_runtime.run_id,
        parent_run_id=parent.resources.run_id,
        stats=child_runtime.last_stats,
    )


__all__ = ["SubflowResult", "run_subflow"]
