"""plan_execute — the same demo as `main.py`, built on `recipes.PlanExecute`
instead of hand-rolled `Planner`/`Executor`/`Finisher` produces.

Compare this file to `produce.py` (141 lines) + `agents.py` (14 lines) +
half of `models.py`: there, the ordering/gating/idempotency logic is
interleaved with the LLM prompts inside each produce. Here `PlanExecute`
owns all of that — this file only implements the three decisions that
actually need judgement: `plan`, `execute_step`, `finish`. Same
offline-friendly fallback style as `main.py` (`structured_llm` returns
`None` when no model is configured; each hook falls back to something
deterministic instead of crashing, §59).

    uv run python -m examples.plan_execute.main_recipe
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import BaseModel
from reactifact import Agent, Artifact, Consume, Context, Runtime, RuntimeResources
from reactifact.providers import LLMProvider
from reactifact.recipes import PlanExecute
from reactifact.structured import structured_llm

from .main import DEFAULT_GOAL, build_llm


class Goal(BaseModel):
    text: str = ""


class PlanStep(BaseModel):
    goal: str = ""
    index: int = 0
    instruction: str = ""


class StepResult(BaseModel):
    goal: str = ""
    index: int = 0
    instruction: str = ""
    output: str = ""


class FinalAnswer(BaseModel):
    goal: str = ""
    text: str = ""


class PlanBody(BaseModel):
    steps: list[str]


class _Text(BaseModel):
    text: str


class Flow(PlanExecute[Goal, PlanStep, StepResult, FinalAnswer]):
    goal_type = Goal
    step_type = PlanStep
    result_type = StepResult
    final_type = FinalAnswer

    async def plan(self, context: Context, goal: Artifact[Goal]) -> list[PlanStep]:
        body = await structured_llm(
            context,
            schema=PlanBody,
            system="Break the goal into at most 3 short, concrete steps.",
            user=goal.data.text,
        )
        steps = (
            body.steps[:3]
            if body is not None and body.steps
            else [
                f"Research: {goal.data.text}",
                f"Draft an answer for: {goal.data.text}",
            ]
        )
        return [PlanStep(instruction=s) for s in steps]

    async def execute_step(
        self,
        context: Context,
        step: Artifact[PlanStep],
        history: list[Artifact[StepResult]],
    ) -> StepResult:
        transcript = "\n".join(f"{r.data.index}. {r.data.output}" for r in history)
        body = await structured_llm(
            context,
            schema=_Text,
            system=(
                "Execute this step, using prior results if relevant:\n"
                f"{transcript or '(none yet)'}"
            ),
            user=step.data.instruction,
        )
        output = body.text if body is not None else f"(offline) {step.data.instruction}"
        return StepResult(instruction=step.data.instruction, output=output)

    async def finish(
        self,
        context: Context,
        goal: Artifact[Goal] | None,
        results: list[Artifact[StepResult]],
    ) -> FinalAnswer:
        transcript = "\n".join(
            f"{r.data.index}. {r.data.instruction} -> {r.data.output}" for r in results
        )
        body = await structured_llm(
            context,
            schema=_Text,
            system=f"Summarize into one final answer for: {goal.data.text if goal else ''}",
            user=transcript,
        )
        text = body.text if body is not None else f"(offline finish)\n{transcript}"
        return FinalAnswer(text=text)


class FlowAgent(Agent):
    name = "plan_execute_recipe"
    consumes = [Consume(Goal), Consume(PlanStep), Consume(StepResult)]
    produces = Flow().produces()


def run(*, text: str = DEFAULT_GOAL, llm: LLMProvider | None = None) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Goal(text=text))
    asyncio.run(Runtime(ctx, agents=[FlowAgent()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.plan_execute.main_recipe")
    parser.add_argument("--text", default=DEFAULT_GOAL)
    args = parser.parse_args()

    ctx = run(text=args.text, llm=build_llm())
    steps = sorted(ctx.list_artifacts(PlanStep), key=lambda s: s.data.index)
    results = {r.data.index: r for r in ctx.list_artifacts(StepResult)}
    finals = ctx.list_artifacts(FinalAnswer)
    print("plan_execute (recipe) · plan → execute steps sequentially → finish")
    for s in steps:
        r = results.get(s.data.index)
        print(f"  [{s.data.index}] {s.data.instruction}")
        if r is not None:
            print(f"      -> {r.data.output}")
    for f in finals:
        print(f"  final: {f.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
