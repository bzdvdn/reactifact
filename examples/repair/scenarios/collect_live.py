"""Record once against the real OpenRouter model (`--mode record`), then
replay that exact recording forever after (`--mode replay`), fully offline —
proving the real model's structured output still parses, without needing a
key in CI. With no `--mode` flag it skips itself rather than guessing."""

from __future__ import annotations

from reactifact.providers import LLMProvider, openrouter_llm
from reactifact.testing import ScenarioLab, ScenarioSkip, mode_from_env, scenario

from ..models import Project, UserMsg
from ..step_groups import RepairFlow
from ._common import FIXTURE, ModelStub, load_recorded_model, resources

ROOM_DESCRIPTION = "Кухня 12 кв.м, потолок 2.7м, бюджет 150000 рублей, стиль минимализм"


@scenario("repair: collect stage against the real model (record/replay)")
async def collect_stage_against_the_model() -> None:
    mode = mode_from_env()
    if mode == "record":
        llm: LLMProvider | None = openrouter_llm()
        if llm is None:
            raise ScenarioSkip("OPENROUTER_API_KEY not set — nothing to record against")
    elif mode == "replay":
        if not FIXTURE.exists():
            raise ScenarioSkip(
                f"no recording at {FIXTURE} yet — run with --mode record once "
                "(with OPENROUTER_API_KEY set), then commit the fixture"
            )
        llm = ModelStub(load_recorded_model(FIXTURE))
    else:
        raise ScenarioSkip(
            "pass --mode record (hits the real model) or --mode replay "
            "(offline, from the committed fixture) to run this scenario"
        )

    lab = ScenarioLab(
        [RepairFlow()],
        resources=lambda: resources(llm),
        mode=mode,
        recording_path=FIXTURE,
    )
    result = await lab.run(UserMsg(text=ROOM_DESCRIPTION))

    project = result.artifacts(Project).exists()
    assert project.stage == "design_choice"
    assert project.design_options
