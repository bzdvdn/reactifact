"""Pipeline definitions: reactive `Pipeline` and ordered `StagedPipeline`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..agent import Agent, Step
from ..agent.agent import AgentSource, normalize_units
from .schedule import ReactiveSchedule, Schedule, Stage, StagedSchedule

if TYPE_CHECKING:
    from ..contracts.report import ContractReport
    from ..validate import ValidationReport


def _unique_agents(agents: Sequence[Agent]) -> list[Agent]:
    """One entry per agent identity, preserving order."""
    seen: set[int] = set()
    unique: list[Agent] = []
    for agent in agents:
        if id(agent) in seen:
            continue
        seen.add(id(agent))
        unique.append(agent)
    return unique


class PipelineDefinition:
    """A reusable agent pipeline definition.

    The canonical form is ``Pipeline(agents=[...])``. Each element is an
    `Agent` or an `AgentSource` — anything with `.agents()`, e.g.
    `recipes.Route` — expanded eagerly and in list order. ``steps=`` remains
    the low-level shorthand; both normalize into ``pipeline.agents``.

    `Pipeline` is the reactive default; `StagedPipeline` adds ordered stages
    with quiescence barriers. Both own only the definition and seed/answer
    models; runtime settings belong to `PipelineRunner`/`RunConfig`.
    """

    def __init__(
        self,
        steps: Sequence[Step[Any]] | None = None,
        *,
        name: str | None = None,
        agents: Sequence[Agent | AgentSource] | None = None,
        seed_type: type[BaseModel] | None = None,
        seed_field: str = "text",
        answer_type: type[BaseModel] | None = None,
    ):
        if steps is not None and agents is not None:
            raise TypeError("Pipeline takes either steps= or agents=, not both")
        if steps is None and not agents:
            raise TypeError("Pipeline requires steps= or agents=")
        self.name = name or "pipeline"
        self.steps: list[Step[Any]] | None = list(steps) if steps is not None else None
        self.agents = normalize_units(
            steps=list(steps) if steps is not None else None,
            agents=list(agents) if agents is not None else None,
        )
        self.seed_type = seed_type
        self.seed_field = seed_field
        self.answer_type = answer_type

    def _reported_agents(self) -> list[Agent]:
        """Agents as they appear in contracts/traces (one entry each)."""
        return self.agents

    def validate(
        self,
        *,
        seeds: Sequence[type | str] = (),
        terminal: Sequence[type | str] = (),
    ) -> ValidationReport:
        """Static contract check for this pipeline (no run, no LLM).

        `seed_type`/`answer_type` are used as the default seeds/terminal when
        set; pass `seeds=`/`terminal=` to override them. This is the same
        `validate_contracts` the CLI and the HTTP adapter use at startup.
        """
        from ..validate import validate_contracts

        inferred_seeds: list[type | str] = list(seeds)
        inferred_terminal: list[type | str] = list(terminal)
        if not inferred_seeds and self.seed_type is not None:
            inferred_seeds.append(self.seed_type)
        if not inferred_terminal and self.answer_type is not None:
            inferred_terminal.append(self.answer_type)
        return validate_contracts(
            agents=self._reported_agents(),
            seeds=inferred_seeds,
            terminal=inferred_terminal,
        )

    def describe(self) -> ContractReport:
        """The static contract plus seed/answer JSON Schemas.

        The transport-neutral "OpenAPI": the CLI prints it, the HTTP adapter
        serves it as `/api/contracts`, and MCP/tool adapters read the same
        models. It never runs the pipeline.
        """
        from ..contracts.report import ContractReport, describe_contracts

        base = describe_contracts(self._reported_agents())
        return ContractReport(
            agents=base.agents,
            seed_schema=(
                self.seed_type.model_json_schema()
                if self.seed_type is not None
                else None
            ),
            answer_schema=(
                self.answer_type.model_json_schema()
                if self.answer_type is not None
                else None
            ),
        )

    def mermaid(self, *, with_stages: bool = True) -> str:
        """Render this pipeline's declared agents as a Mermaid flowchart."""
        from ..viz import blueprint

        return blueprint(
            self._reported_agents(), with_stages=with_stages, title=self.name
        )

    def schedule(self) -> Schedule:
        """The turn execution strategy (reactive by default)."""
        return ReactiveSchedule()


class Pipeline(PipelineDefinition):
    """The reactive default: agents run to a fixpoint over all events."""


class StagedPipeline(PipelineDefinition):
    """Ordered stages with a quiescence barrier between them.

    Agents inside a stage stay fully reactive; later stages wait until the
    current one settles. `Stage.reopen=True` lets a later event re-enter an
    earlier stage, bounded by the turn's generation budget.
    """

    def __init__(
        self,
        stages: Sequence[Stage],
        *,
        name: str | None = None,
        seed_type: type[BaseModel] | None = None,
        seed_field: str = "text",
        answer_type: type[BaseModel] | None = None,
    ):
        stages = tuple(stages)
        if not stages:
            raise TypeError("StagedPipeline requires stages=[Stage(...), ...]")
        self.name = name or "staged"
        self.stages = stages
        self.steps: list[Step[Any]] | None = None
        self.agents = [agent for stage in stages for agent in stage.agents]
        self.seed_type = seed_type
        self.seed_field = seed_field
        self.answer_type = answer_type

    def _reported_agents(self) -> list[Agent]:
        # The same agent may sit in two stages; its contract is stage-independent.
        return _unique_agents(self.agents)

    def schedule(self) -> Schedule:
        return StagedSchedule(self.stages)


__all__ = [
    "Pipeline",
    "PipelineDefinition",
    "StagedPipeline",
]
