"""plan_execute demo: prompts and the offline (no-LLM) fallback plan."""

from __future__ import annotations

from reactifact.prompts import PromptTemplate

MAX_STEPS = 4

PLAN = PromptTemplate(
    """Break the goal down into at most {max_steps} short, ordered,
actionable steps. Each step should build on the previous ones."""
)
EXECUTE = PromptTemplate(
    """You are executing step {index} of a plan toward this goal: {goal}
Steps completed so far:
{history}

Now do this step and report only its outcome:
{instruction}"""
)
FINISH = PromptTemplate(
    """Combine the following step-by-step transcript into one final answer
for the goal: {goal}
{transcript}"""
)


def fallback_plan(text: str) -> list[str]:
    """Deterministic single-step plan when no LLM is configured."""
    return [f"Address the goal directly: {text}"]
