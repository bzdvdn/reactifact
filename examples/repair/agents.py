"""Repair assistant agent — a container over the staged Step (§71)."""

from reactifact import Agent

from .steps import (
    ApprovalStage,
    AssistantStage,
    CollectStage,
    EstimateStage,
    PickStage,
    PlanStage,
)


class RepairFlow(Agent):
    """Single dispatcher: reacts to messages and project changes.

    Each stage is its own Step with a deterministic guard; routing follows
    `Project.stage` — the runtime builds links from the artifact state.
    """

    name = "repair_flow"
    steps = [
        CollectStage(),
        PickStage(),
        PlanStage(),
        EstimateStage(),
        ApprovalStage(),
        AssistantStage(),
    ]
