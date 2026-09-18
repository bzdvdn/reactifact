"""reflection — generate → critique → regenerate (LangGraph "Reflection" port).

The classic reflection loop, expressed with effects: a `Draft` artifact is
produced, a `Critic` produce scores it (structured LLM), and a `Regenerator`
improves it with the feedback — deterministically capped by rounds and by an
"accepted" status. Working memory is artifacts, not a chat buffer:
`Draft`/`Review`/`Final` accumulate as the runtime iterates.

    uv run python -m examples.reflection.main
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

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


MAX_ROUNDS = 2  # how many rewrites are allowed
ACCEPT_AT = 0.8  # critic score that ends the loop


class Topic(BaseModel):
    text: str


class Draft(BaseModel):
    text: str
    round: int = 0
    status: str = "draft"  # draft | accepted


class ReviewBody(BaseModel):
    score: float
    feedback: str


class Review(BaseModel):
    round: int
    score: float
    feedback: str


class Final(BaseModel):
    text: str
    rounds: int


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


class DraftIt(Produce[Draft]):
    artifact_type = Draft

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        topic = next((t for t in context.list_artifacts(Topic)), None)
        if topic is None:
            return None
        if context.get(f"draft:{topic.id}") is not None:
            return None
        text = await _call(
            context,
            schema=_Reply,
            system=_DRAFT.render(topic=topic.data.text),
            user=topic.data.text,
        )
        self.effects.create(
            Draft(text=text, round=0, status="draft"), id=f"draft:{topic.id}"
        )
        return None


class Critic(Produce[Review]):
    artifact_type = Review

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        topic = next((t for t in context.list_artifacts(Topic)), None)
        draft = next((d for d in context.list_artifacts(Draft)), None)
        if topic is None or draft is None or draft.data.status != "draft":
            return None
        review = await structured_llm(
            context,
            schema=ReviewBody,
            system=_CRITIC.render(topic=topic.data.text),
            user=f"Draft: {draft.data.text}",
        )
        if review is None:  # offline / honest fallback (§59)
            review = ReviewBody(
                score=0.4, feedback="(offline) revise to be more concrete"
            )
        self.effects.create(
            Review(
                round=draft.data.round, score=review.score, feedback=review.feedback
            ),
            id=f"review:{draft.id}:{draft.data.round}",
        )
        if review.score >= ACCEPT_AT:
            self.effects.update(draft, status="accepted")
        return None


class Rewrite(Produce[Draft]):
    artifact_type = Draft

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        draft = next((d for d in context.list_artifacts(Draft)), None)
        review = next((r for r in context.list_artifacts(Review)), None)
        topic = next((t for t in context.list_artifacts(Topic)), None)
        if draft is None or review is None or topic is None:
            return None
        if draft.data.status == "accepted" or draft.data.round + 1 > MAX_ROUNDS:
            return None
        improved = await _call(
            context,
            schema=_Reply,
            system=_REWRITE.render(
                topic=topic.data.text,
                draft=draft.data.text,
                feedback=review.data.feedback,
            ),
            user=topic.data.text,
        )
        self.effects.update(
            draft, text=improved, round=draft.data.round + 1, status="draft"
        )
        return None


class Finalize(Produce[Final]):
    artifact_type = Final

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        draft = next((d for d in context.list_artifacts(Draft)), None)
        if draft is None:
            return None
        done = draft.data.status == "accepted" or draft.data.round >= MAX_ROUNDS
        if not done:
            return None
        if context.get(f"final:{draft.id}") is not None:
            return None
        self.effects.create(
            Final(text=draft.data.text, rounds=draft.data.round),
            id=f"final:{draft.id}",
        )
        return None


class _Reply(BaseModel):
    text: str


async def _call(context: Context, *, schema: type[Any], system: str, user: str) -> str:
    body = await structured_llm(context, schema=schema, system=system, user=user)
    return (
        body.text
        if body is not None
        else f"(not configured or call failed) {user[:80]}"
    )


class Flow(Agent):
    name = "reflection"
    consumes = [Consume(Topic), Consume(Draft), Consume(Review)]
    produces = [DraftIt(), Critic(), Rewrite(), Finalize()]


def run(
    *, topic: str = "Hydropower: pros and cons", llm: LLMProvider | None = None
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Topic(text=topic))
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.reflection.main")
    parser.add_argument("--topic", default="Hydropower: pros and cons")
    args = parser.parse_args()

    ctx = run(topic=args.topic, llm=build_llm())
    finals = ctx.list_artifacts(Final)
    print("reflection · generate → critique → regenerate")
    for f in finals:
        print(f"  final (round {f.data.rounds}):")
        print(f"    {f.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
