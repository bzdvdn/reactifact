"""Multi-turn: the full repair flow — facts -> design pick -> plan -> estimate
-> approval — across separate turns on one shared `Context`, using
`lab.scenario()` (§60 HITL).

`RepairFlow` is a *single* agent whose internal stages route by
`Project.stage`, not by agent identity (see `agents.py`) — so `Scenario.path`
would only ever show `repair_flow` repeated, never the stage names. The
meaningful "path" here is the stage machine itself, so this scenario asserts
`Project.stage` after each turn instead of the agent path.

Fact extraction from free text (`CollectStage`'s `_extract_info`) genuinely
needs an LLM (§59: no model configured -> honest "current info unchanged"
fallback, not a guess) — so, like `estimate_prices_from_catalog`, this
scenario seeds the facts directly instead of parsing them from a sentence,
keeping the whole multi-turn walk deterministic and LLM-free.
"""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import RepairFlow
from ..models import Estimate, Project, ProjectInfo, UserMsg
from ..services.geometry import ensure_geometry
from ._common import resources

ROOM_INFO = ensure_geometry(
    ProjectInfo(
        room_type="кухня",
        area=12,
        ceiling_height=2.7,
        budget=150_000,
        style="минимализм",
    )
)


@scenario("repair: full flow from facts to approval (multi-turn)")
async def full_flow_reaches_approval() -> None:
    lab = ScenarioLab([RepairFlow], resources=resources)
    convo = lab.scenario()

    # turn 1: facts already complete -> design options offered directly
    r1 = await convo.turn(
        Project(stage="collect", info=ROOM_INFO), UserMsg(text="вот мои пожелания")
    )
    project = r1.artifacts(Project).exists()
    assert project.stage == "design_choice"
    assert project.design_options

    # turn 2: pick option 1 -> plan (LLM/fallback) + estimate (catalog), both
    # run automatically within this same turn since neither needs new input
    r2 = await convo.turn(UserMsg(text="1"))
    project = r2.artifacts(Project).exists()
    assert project.stage == "final_approval"
    assert project.plan
    assert isinstance(project.estimate, Estimate)
    assert project.estimate.lines

    # turn 3: approve -> assistant
    r3 = await convo.turn(UserMsg(text="да, согласен"))
    project = r3.artifacts(Project).exists()
    assert project.stage == "assistant"
    assert project.approved is True

    convo.path.contains("repair_flow")  # sanity: the agent ran on every turn
