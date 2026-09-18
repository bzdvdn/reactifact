"""llm-ladder · level 3 — lifecycle, multi-call, and state-changing patches (§67, §69).

Three LLM calls across the turn, several produces, and a `StatusMachine` that
*moves state* (its transitions are `update_fields` patches). The turn artifacts
are the working memory; the patches accumulate:

    Question    ──► Turn(status="new")
    Turn        ──► Claim (LLM) ──for_turn──▶ Turn
    Claim       ──► StatusMachine: Turn(status="answered")   ← update patch
    answered    ──► Answer (LLM) ──supported_by──▶ Claim

Teachings:

- state changes are patches (`update_fields`), driven deterministically by a
  pure `next_status` (§69) — no manual graph;
- multi-step flows share working memory via artifacts, not a chat buffer;
- each produce stays small: one concern, one guard, one effect-set;
- the machine's `terminal` statuses stop the loop (§69).

    uv run python -m examples.llm_ladder.level3
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
from reactifact.recipes import StatusMachine, find
from reactifact.structured import structured_llm


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


class Turn(BaseModel):
    query_id: str
    status: str = "new"


class Claim(BaseModel):
    query_id: str
    text: str
    confidence: float = 0.7


class Answer(BaseModel):
    query_id: str
    text: str


class ClaimBody(BaseModel):
    text: str
    confidence: float = 0.7


class AnswerBody(BaseModel):
    text: str


_CLAIM = PromptTemplate(
    """You are a fact-checker in the domain of {topic}.
State ONE claim relevant to "{question}", with a confidence 0..1 if you are
confident. No hedging."""
)
_SYNTHESIS = PromptTemplate(
    """You are an examiner in the domain of {topic}.
Given the verified claim, write a one-sentence final answer to "{question}".
Do not invent new facts."""
)

TURN_PLACEHOLDER = "turn:{qid}"


class StartTurn(Produce[Turn]):
    """Question → a fresh working-memory Turn (§42 idempotent)."""

    artifact_type = Turn

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question = find(call.inputs, Question)
        if question is None:
            return None
        qid = question.id
        if context.get(f"turn:{qid}") is not None:
            return None
        self.effects.create(Turn(query_id=qid, status="new"), id=f"turn:{qid}")
        return None


class Claimer(Produce[Claim]):
    """Turn → a structured claim (LLM), linked to the turn (§34)."""

    artifact_type = Claim

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question = find(call.inputs, Question)
        turn = find(call.inputs, Turn)
        if question is None or turn is None:
            return None
        qid = question.id
        if context.get(f"claim:{qid}") is not None:
            return None

        body = await structured_llm(
            context,
            schema=ClaimBody,
            system=_CLAIM.render(
                topic=question.data.topic, question=question.data.text
            ),
            user=f"{question.data.text}\n(no LLM configured → fallback claim)",
        )
        text = (
            body.text if body is not None else f"(offline claim) {question.data.text}"
        )
        confidence = body.confidence if body is not None else 0.5
        claim = self.effects.create(
            Claim(query_id=qid, text=text, confidence=confidence), id=f"claim:{qid}"
        )
        claim.link("for_turn", turn)  # turn: Artifact
        return None


class EvaluateTurn(StatusMachine[Turn]):
    """Turn lifecycle: the claim makes the turn answerable (§69)."""

    artifact_type = Turn
    terminal = frozenset({"answered"})

    def next_status(self, context: Context, key: str) -> str | None:
        if any(c.data.query_id == key for c in context.list_artifacts(Claim)):
            return "answered"
        return None


class Finisher(Produce[Answer]):
    """Answered turn → final answer (LLM), linked to its claim (§34)."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question = find(call.inputs, Question)
        turn = find(call.inputs, Turn)
        claim = find(call.inputs, Claim)
        if question is None or turn is None or claim is None:
            return None
        qid = question.id
        if turn.data.status != "answered":
            return None
        if context.get(f"answer:{qid}") is not None:
            return None

        body = await structured_llm(
            context,
            schema=AnswerBody,
            system=_SYNTHESIS.render(
                topic=question.data.topic, question=question.data.text
            ),
            user=f"Verified claim: {claim.data.text}",
        )
        text = (
            body.text
            if body is not None
            else f"No LLM configured to synthesize an answer; claim on record: {claim.data.text}"
        )
        answer = self.effects.create(
            Answer(query_id=qid, text=text), id=f"answer:{qid}"
        )
        answer.link("supported_by", claim)  # claim: Artifact
        return None


class TurnAgent(Agent):
    name = "turn"
    consumes = [Consume(Question), Consume(Turn), Consume(Claim)]
    produces = [
        StartTurn(),
        Claimer(),
        EvaluateTurn(),
        Produce(Claim),
        Finisher(),
    ]


def run(
    *,
    question: str = "Is the electron wave or particle?",
    topic: str = "quantum physics",
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Question(text=question, topic=topic))
    asyncio.run(Runtime(ctx, agents=[TurnAgent()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.llm_ladder.level3")
    parser.add_argument("--question", default="Is the electron wave or particle?")
    parser.add_argument("--topic", default="quantum physics")
    args = parser.parse_args()

    ctx = run(question=args.question, topic=args.topic, llm=build_llm())
    turns = ctx.list_artifacts(Turn)
    claims = ctx.list_artifacts(Claim)
    answers = ctx.list_artifacts(Answer)
    print("level 3 · lifecycle: Turn → Claim → answered → Answer")
    for t in turns:
        print(f"  turn:     status={t.data.status} ({t.id})")
    for c in claims:
        print(f"  claim:    {c.data.text}  (conf {c.data.confidence:g})")
    for a in answers:
        print(f"  answer:   {a.data.text}")
        linked = ctx.related(a.id, relation="supported_by")
        print(f"  sources:  {[c.id for c in linked]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
