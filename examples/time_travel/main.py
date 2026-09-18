"""time_travel — branch from the middle, run experiments, merge (§39-§40).

After a milestone commit, the app forks the context ("time-travel") and runs
two candidate experiments in parallel — each on its own branch. The branches
are then merged three-way, and a deterministic picker chooses the better
candidate and links the decision back to **both** (provenance is kept). The
same root lets you replay to the middle with `python -m reactifact replay`.

    uv run python -m examples.time_travel.main
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.prompts import PromptTemplate
from reactifact.providers import LLMProvider
from reactifact.structured import structured_llm


def build_llm() -> LLMProvider | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured -> offline."""
    import os

    from reactifact.providers import openai_llm, openrouter_llm

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    return None


class Request(BaseModel):
    text: str


class Milestone(BaseModel):
    status: str = "drafted"
    note: str = ""


class Trigger(BaseModel):
    name: str


class Candidate(BaseModel):
    name: str
    score: float
    text: str


class Review(BaseModel):
    pass


class Decision(BaseModel):
    text: str
    sources: list[str] = []


class _Text(BaseModel):
    text: str


_CANDIDATE = PromptTemplate(
    """You draft the '{name}' solution for the request. Answer concisely."""
)


class Stage(Produce[Milestone]):
    artifact_type = Milestone

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        request = call.trigger
        if request is None or not isinstance(request.data, Request):
            return None
        if context.list_artifacts(Milestone):
            return None
        self.effects.create(
            Milestone(status="drafted", note=f"milestone for {request.id}")
        )
        return None


class Experiment(Produce[Candidate]):
    # ExperimentAgent also consumes Request (to read it, not to re-run on
    # its own arrival) — reacts_to keeps this produce to Trigger events only;
    # `trigger` (the parameter) gets the resolved Trigger (the model)
    # directly, guaranteed non-None.
    artifact_type = Candidate
    reacts_to = (Trigger,)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        trigger = call.trigger
        assert trigger is not None
        request = next((r for r in context.list_artifacts(Request)), None)
        if request is None:
            return None
        body = await structured_llm(
            context,
            schema=_Text,
            system=_CANDIDATE.render(name=trigger.data.name),
            user=request.data.text if request is not None else "",
        )
        text = (
            body.text
            if body is not None
            else f"(offline {trigger.data.name}) «{request.data.text[:60]}»"
        )
        candidate = self.effects.create(
            Candidate(
                name=trigger.data.name, score=_score_of(trigger.data.name), text=text
            ),
            id=f"candidate:{trigger.data.name}",
        )
        candidate.link("for_request", request.id)
        return None


def _score_of(name: str) -> float:
    return 0.9 if name == "a" else 0.6  # deterministic ranking (§67)


class Pick(Produce[Decision]):
    artifact_type = Decision

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        if context.list_artifacts(Decision):
            return None
        candidates = sorted(
            context.list_artifacts(Candidate), key=lambda c: c.data.score, reverse=True
        )
        if not candidates:
            return None
        best = candidates[0]
        decision = self.effects.create(
            Decision(
                text=best.data.text,
                sources=[c.id for c in candidates],
            ),
            id="decision:1",
        )
        for candidate in candidates:
            decision.link("supported_by", candidate)
        return None


class StageAgent(Agent):
    name = "stage"
    consumes = [Consume(Request)]
    produces = [Stage()]


class ExperimentAgent(Agent):
    name = "experiment"
    consumes = [Consume(Trigger), Consume(Request)]
    produces = [Experiment()]


class PickAgent(Agent):
    name = "picker"
    consumes = [Consume(Review)]
    produces = [Pick()]


def _arun(runtime: Runtime) -> None:
    asyncio.run(runtime.arun())


def run(
    *,
    text: str = "Choose how to present the renovation plan.",
    llm: LLMProvider | None = None,
) -> Context:
    base = Context(resources=RuntimeResources(llm=llm))
    request = base.create(Request(text=text))
    _arun(Runtime(base, agents=[StageAgent()]))  # milestone commit (the middle)

    fork_a = base.branch(name="a")
    fork_b = base.branch(name="b")
    fork_a.create(Trigger(name="a"))
    fork_b.create(Trigger(name="b"))
    _arun(Runtime(fork_a, agents=[ExperimentAgent()]))
    _arun(Runtime(fork_b, agents=[ExperimentAgent()]))

    fork_a.merge(fork_b)  # three-way, §40
    fork_a.create(Review())
    _arun(Runtime(fork_a, agents=[PickAgent()]))
    _ = request
    return fork_a


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.time_travel.main")
    parser.parse_args()

    merged = run(llm=build_llm())
    candidates = sorted(
        merged.list_artifacts(Candidate), key=lambda c: c.data.score, reverse=True
    )
    decisions = merged.list_artifacts(Decision)
    print("time_travel · milestone → fork → parallel experiments → merge → pick")
    print(f"  merged version: v{merged.version}")
    for c in candidates:
        print(f"  [{c.data.name} {c.data.score:.2f}] {c.data.text[:80]}")
    for d in decisions:
        print(f"  decision: {d.data.text[:80]}")
        print(f"  supported_by: {d.data.sources}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
