"""Record once against the real model (`--mode record`), then replay that
exact recording forever after (`--mode replay`), fully offline — proving the
real model actually assembles a coherent, sourced answer from the retrieved
evidence, without needing a key in CI."""

from __future__ import annotations

from typing import Any

from reactifact.testing import ScenarioLab, ScenarioSkip, mode_from_env, scenario

from ..agents import AGENTS
from ..chat import build_llm
from ..models import Answer, UserQuery
from ._common import (
    FIXTURE,
    GPU_COST_QUESTION,
    ModelStub,
    load_recorded_model,
    resources,
)


@scenario("knowledge: research answer against the real model (record/replay)")
async def research_answer_against_the_model() -> None:
    mode = mode_from_env()
    llm: Any
    if mode == "record":
        llm = build_llm()
        if llm is None:
            raise ScenarioSkip(
                "no LLM configured — set OPENROUTER_API_KEY (or "
                "OPENAI_BASE_URL/OPENAI_MODEL) to record against a real model"
            )
    elif mode == "replay":
        if not FIXTURE.exists():
            raise ScenarioSkip(
                f"no recording at {FIXTURE} yet — run with --mode record once "
                "(with a model configured), then commit the fixture"
            )
        llm = ModelStub(load_recorded_model(FIXTURE))
    else:
        raise ScenarioSkip(
            "pass --mode record (hits the real model) or --mode replay "
            "(offline, from the committed fixture) to run this scenario"
        )

    lab = ScenarioLab(
        list(AGENTS),
        resources=lambda: resources(llm),
        mode=mode,
        recording_path=FIXTURE,
    )
    result = await lab.run(UserQuery(text=GPU_COST_QUESTION))

    answer = result.artifacts(Answer).exists()
    assert answer.text.strip()
    assert answer.sources  # the model's answer is still grounded in evidence
