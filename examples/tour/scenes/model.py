"""An LLM-backed step with an honest deterministic fallback."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import (
    Artifact,
    Budget,
    Context,
    Effects,
    Runtime,
    RuntimeResources,
    create_agent,
    step,
)
from reactifact.providers import LLMProvider
from reactifact.structured import llm_reply


class TourFacts(BaseModel):
    lines: list[str]


class TourBrief(BaseModel):
    text: str


@step(produces=TourBrief, consumes=TourFacts)
async def write_brief(
    context: Context,
    facts: Artifact[TourFacts],
    effects: Effects,
) -> None:
    prompt = "Summarize these framework tour facts in one sentence:\n" + "\n".join(
        f"- {line}" for line in facts.data.lines
    )
    text = await llm_reply(
        context,
        system="You are a precise technical writer. Reply with one sentence.",
        user=prompt,
    )
    if text is None:
        text = "Offline tour: " + "; ".join(facts.data.lines)
    effects.create(TourBrief(text=text.strip()), id="brief:1")


async def run_model(provider: LLMProvider | None) -> dict[str, Any]:
    context = Context(resources=RuntimeResources(llm=provider))
    context.create(
        TourFacts(
            lines=[
                "steps react to typed artifacts",
                "effects compile into atomic patches",
                "sessions resume from the last committed generation",
            ]
        ),
        id="facts:1",
    )
    runtime = Runtime(
        context,
        agents=[create_agent("model", steps=[write_brief])],
        budget=Budget(max_runs=10),
    )
    await runtime.arun()
    brief = context.latest(TourBrief)
    assert brief is not None
    return {
        "mode": "model" if provider is not None else "offline",
        "brief": brief.data.text,
    }
