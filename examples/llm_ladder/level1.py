"""llm-ladder · level 1 — the simplest LLM turn (§67, §68).

One question in, one `Answer` out. Teachings:

- a guard returns `None` when the work is already done (idempotency, §42);
- one `structured_llm` call with a schema (the deterministic parsing stays in
  code, the model only reasons);
- the produce writes `self.effects.create(Answer)` and returns `None` —
  `create` is the only effect vocabulary needed here; `link`/`update` join in
  at levels 2-3;
- without a model, `structured_llm` returns `None` and the fallback line answers
  honestly (offline mode, §59).

    uv run python -m examples.llm_ladder.level1            # offline (no .env)
    uv run python -m examples.llm_ladder.level1 --topic ... # model mode via .env
"""

from __future__ import annotations

import argparse
import sys

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.prompts import PromptTemplate
from reactifact.providers import LLMProvider
from reactifact.recipes import find


def build_llm() -> LLMProvider | None:
    """Explicit provider for this level: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured → offline."""
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


class Question(BaseModel):
    text: str
    topic: str = "general knowledge"


class Answer(BaseModel):
    text: str


class AnswerBody(BaseModel):
    """The only schema in this level: the model returns a `text`."""

    text: str


_SYSTEM = PromptTemplate(
    """You answer a short question in the domain of {topic}.
Strictly one concise sentence; no hedging; no invented facts."""
)


class Answerer(Produce[Answer]):
    """Answers a question; returns None when an answer already exists (§42)."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question = find(call.inputs, Question)
        if question is None:
            return None
        qid = question.id
        if context.get(f"answer:{qid}") is not None:
            return None  # already answered — idempotent re-run

        body = await structured_reply(context, question)
        self.effects.create(Answer(text=body), id=f"answer:{qid}")
        return None


async def structured_reply(context: Context, question: Artifact[Question]) -> str:
    from reactifact.structured import structured_llm

    body = await structured_llm(
        context,
        schema=AnswerBody,
        system=_SYSTEM.render(topic=question.data.topic),
        user=question.data.text,
    )
    if body is not None:
        return body.text
    return (
        "(no answer) — the model is not configured or the call failed; check logs "
        f"— the question was: {question.data.text}"
    )


class OneTurnAgent(Agent):
    name = "one_turn"
    consumes = [Consume(Question)]
    produces = [Answerer()]


def run(
    *,
    question: str = "What are the three states of water?",
    topic: str = "physics",
    llm: LLMProvider | None = None,
) -> Context:
    import asyncio

    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Question(text=question, topic=topic))
    asyncio.run(Runtime(ctx, agents=[OneTurnAgent()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.llm_ladder.level1")
    parser.add_argument("--question", default="What are the three states of water?")
    parser.add_argument("--topic", default="physics")
    args = parser.parse_args()

    ctx = run(question=args.question, topic=args.topic, llm=build_llm())
    answers = ctx.list_artifacts(Answer)
    print("level 1 · one structured call → one artifact")
    for a in answers:
        print(f"  answer: {a.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
