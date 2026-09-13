"""plan_execute — planner drafts steps, executor runs them one at a time,
using each prior step's output (LangChain/AutoGPT-style Plan-and-Execute).

A `Goal` is decomposed into ordered `PlanStep`s (structured LLM plan, or a
deterministic single-step fallback); an executor produce runs strictly one
step per generation — it only advances once the previous step's `StepResult`
exists — feeding the running transcript back into the prompt; once every step
has a result, a finisher produce synthesizes the `FinalAnswer`.

Unlike `map_reduce` (independent chunks fan out in parallel), steps here are
*sequentially dependent*: nothing here changes that — the runtime just leaves
each step ineligible until its precondition (the previous step's result)
exists (§69).

    uv run python -m examples.plan_execute.main
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from reactifact import Context, Runtime, RuntimeResources
from reactifact.providers import LLMProvider

from .agents import Flow
from .models import FinalAnswer, Goal, PlanStep, StepResult


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


DEFAULT_GOAL = "Plan and summarize a weekend trip to the mountains on a tight budget."


def run(
    *,
    text: str = DEFAULT_GOAL,
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Goal(text=text))
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.plan_execute.main")
    parser.add_argument("--text", default=DEFAULT_GOAL)
    args = parser.parse_args()

    ctx = run(text=args.text, llm=build_llm())
    steps = sorted(ctx.list_artifacts(PlanStep), key=lambda s: s.data.index)
    results = {r.data.index: r for r in ctx.list_artifacts(StepResult)}
    finals = ctx.list_artifacts(FinalAnswer)
    print("plan_execute · plan → execute steps sequentially → finish")
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
