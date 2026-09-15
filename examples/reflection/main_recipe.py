"""reflection — the same demo as `main.py`, built on `recipes.ReflectionLoop`
instead of hand-rolled `DraftIt`/`Critic`/`Rewrite`/`Finalize` produces.

`main.py`'s four produces (114 lines) interleave round-capping, the accept
threshold, and idempotency guards with the LLM prompts. `ReflectionLoop`
owns all of that; this file only implements the four decisions that
actually need judgement: `draft`, `critique`, `rewrite`, `finish`.

    uv run python -m examples.reflection.main_recipe [--topic "..."]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import BaseModel
from reactifact import Agent, Artifact, Consume, Context, Runtime, RuntimeResources
from reactifact.prompts import PromptTemplate
from reactifact.providers import LLMProvider
from reactifact.recipes import ReflectionLoop
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


MAX_ROUNDS = 2
ACCEPT_AT = 0.8


class Topic(BaseModel):
    text: str = ""


class Draft(BaseModel):
    topic: str = ""
    round: int = 0
    status: str = "draft"
    text: str = ""


class Review(BaseModel):
    topic: str = ""
    round: int = 0
    score: float = 0.0
    feedback: str = ""


class Final(BaseModel):
    topic: str = ""
    text: str = ""
    rounds: int = 0


class ReviewBody(BaseModel):
    score: float
    feedback: str


class _Reply(BaseModel):
    text: str


_DRAFT = PromptTemplate(
    """You are a writer in the domain described by the topic.
Write a concise first version directly addressing the topic. No hedging."""
)
_CRITIC = PromptTemplate(
    """You are a strict reviewer for the topic.
Score the draft 0..1 and give one sentence of actionable feedback. A score of
0.8 or higher means the draft should be accepted."""
)
_REWRITE = PromptTemplate(
    """You are a writer revising a draft for the topic.

Draft: {draft}
Feedback: {feedback}
Rewrite the draft addressing the feedback; keep it concise."""
)


async def _call(context: Context, *, system: str, user: str) -> str:
    body = await structured_llm(context, schema=_Reply, system=system, user=user)
    return body.text if body is not None else f"(offline) {user[:80]}"


class MyLoop(ReflectionLoop[Topic, Draft, Review, Final]):
    topic_type = Topic
    draft_type = Draft
    review_type = Review
    final_type = Final
    accept_at = ACCEPT_AT
    max_rounds = MAX_ROUNDS

    async def draft(self, context: Context, topic: Artifact[Topic]) -> Draft:
        text = await _call(
            context, system=_DRAFT.render(topic=topic.data.text), user=topic.data.text
        )
        return Draft(text=text)

    async def critique(
        self, context: Context, draft: Artifact[Draft]
    ) -> tuple[float, str]:
        review = await structured_llm(
            context,
            schema=ReviewBody,
            system=_CRITIC.render(topic=draft.data.text),
            user=f"Draft: {draft.data.text}",
        )
        if review is None:  # offline / honest fallback (§59)
            return 0.4, "(offline) revise to be more concrete"
        return review.score, review.feedback

    async def rewrite(
        self, context: Context, draft: Artifact[Draft], feedback: str
    ) -> Draft:
        text = await _call(
            context,
            system=_REWRITE.render(draft=draft.data.text, feedback=feedback, topic=""),
            user=draft.data.text,
        )
        return Draft(text=text)

    async def finish(self, context: Context, draft: Artifact[Draft]) -> Final:
        return Final(text=draft.data.text, rounds=draft.data.round)


class Flow(Agent):
    name = "reflection_recipe"
    consumes = [Consume(Topic), Consume(Draft), Consume(Review)]
    produces = MyLoop().produces()


def run(
    *, topic: str = "Hydropower: pros and cons", llm: LLMProvider | None = None
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Topic(text=topic))
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.reflection.main_recipe")
    parser.add_argument("--topic", default="Hydropower: pros and cons")
    args = parser.parse_args()

    ctx = run(topic=args.topic, llm=build_llm())
    finals = ctx.list_artifacts(Final)
    print("reflection (recipe) · generate → critique → regenerate")
    for f in finals:
        print(f"  final (round {f.data.rounds}):")
        print(f"    {f.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
