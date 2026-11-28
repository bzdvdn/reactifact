"""Repair assistant agent — a container over the staged Steps (§71)."""

from reactifact import Agent, Consume, Pipeline

from ..models import Project, UserMsg
from ._stages import (
    approval_stage,
    assistant_stage,
    collect_stage,
    estimate_stage,
    pick_stage,
    plan_stage,
)

#: Single dispatcher: reacts to messages and project changes.
#:
#: `consumes` is spelled out even though the runtime could derive triggers from
#: the steps: the agent owns the wake contract, and a reader should not have to
#: inspect six steps to answer "when does the repair flow run?".
#:
#: Each stage is its own Step with a deterministic guard; routing follows
#: `Project.stage` — the runtime builds links from the artifact state.
RepairFlow = Agent(
    "repair_flow",
    consumes=[Consume(UserMsg), Consume(Project)],
    steps=[
        collect_stage,
        pick_stage,
        plan_stage,
        estimate_stage,
        approval_stage,
        assistant_stage,
    ],
)


#: The reusable definition behind both transports.
PIPELINE = Pipeline(agents=[RepairFlow], name="repair")


__all__ = [
    "PIPELINE",
    "RepairFlow",
    "approval_stage",
    "assistant_stage",
    "collect_stage",
    "estimate_stage",
    "pick_stage",
    "plan_stage",
]
