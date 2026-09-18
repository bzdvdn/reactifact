"""llm-ladder · level 2 — provenance and a linked patch (§34, §67, §68).

One question over a source `Doc`. Two LLM calls (word the evidence, synthesize
the answer) and a *richer patch*: two artifacts and two provenance edges in a
single return.

    create Evidence ──extracted_from──▶ Doc
    create Answer    ──supported_by──▶  Evidence

Teachings:

-
  effects group several changes into one atomic patch (create ×2 + link ×2);
- provenance links are first-class, not strings inside the answer (§34);
- deterministic parts (the source id, the score, the citation) stay in code;
  the LLM only words and synthesizes (§67);
- with no model, both `structured_llm` calls return `None` → honest fallbacks.

    uv run python -m examples.llm_ladder.level2
"""

from __future__ import annotations

import argparse
import asyncio
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


#: A tiny "knowledge base" shipped with the demo (a source document).
KNOWLEDGE = (
    "Hydropower converts falling water into electricity; grid-scale pumped "
    "storage round-trips roughly 70-80% of the energy."
)


class Question(BaseModel):
    text: str
    topic: str = "energy systems"


class Doc(BaseModel):
    path: str
    text: str


class Evidence(BaseModel):
    query_id: str
    source: str
    text: str
    score: float = 0.8


class Answer(BaseModel):
    query_id: str
    text: str
    sources: list[str] = []


class EvidenceBody(BaseModel):
    text: str


class AnswerBody(BaseModel):
    text: str


_WORDING = PromptTemplate(
    """You are a reader in the domain of {topic}.
Condense the document snippet into one evidence sentence that answers
"{question}". No new facts; no numbers you did not see."""
)
_SYNTHESIS = PromptTemplate(
    """You are a lead analyst in the domain of {topic}.
Write the final answer to "{question}" from the evidence sentence. Cite the
source; be self-contained."""
)


class AnswerFromDoc(Produce[Answer]):
    """Word the evidence, synthesize the answer, link everything (§34)."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question = find(call.inputs, Question)
        doc = find(call.inputs, Doc)
        if question is None or doc is None:
            return None
        qid = question.id
        if context.get(f"answer:{qid}") is not None:
            return None

        wording = await _evidence_text(context, question, doc)
        answer_text = await _answer_text(context, question, wording)

        # effects: plan the artifacts and link them by handles (§38); the runtime
        # compiles this slot into one atomic patch at the end of the produce.
        evidence = self.effects.create(
            Evidence(query_id=qid, source=doc.data.path, text=wording),
            id=f"evidence:{qid}",
        )
        answer = self.effects.create(
            Answer(query_id=qid, text=answer_text, sources=[doc.data.path]),
            id=f"answer:{qid}",
        )
        evidence.link("extracted_from", doc)  # doc: Artifact
        answer.link("supported_by", evidence)  # evidence: Handle
        return None


async def _evidence_text(
    context: Context, question: Artifact[Question], doc: Artifact[Doc]
) -> str:
    body = await structured_llm(
        context,
        schema=EvidenceBody,
        system=_WORDING.render(topic=question.data.topic, question=question.data.text),
        user=question.data.text + "\n\n" + doc.data.text,
    )
    return body.text if body is not None else doc.data.text[:200]


async def _answer_text(
    context: Context, question: Artifact[Question], wording: str
) -> str:
    body = await structured_llm(
        context,
        schema=AnswerBody,
        system=_SYNTHESIS.render(
            topic=question.data.topic, question=question.data.text
        ),
        user=f"{question.data.text}\nEvidence: {wording}",
    )
    return body.text if body is not None else f"(offline synthesis) {wording}"


class DocAgent(Agent):
    name = "doc_answer"
    consumes = [Consume(Question), Consume(Doc)]
    produces = [AnswerFromDoc(), Produce(Evidence)]


def run(
    *,
    question: str = "How efficient is grid-scale pumped storage?",
    topic: str = "energy systems",
    source_text: str = KNOWLEDGE,
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    doc = ctx.create(Doc(path="docs/hydro.md", text=source_text))
    q = ctx.create(Question(text=question, topic=topic))
    _ = (doc, q)
    asyncio.run(Runtime(ctx, agents=[DocAgent()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.llm_ladder.level2")
    parser.add_argument(
        "--question", default="How efficient is grid-scale pumped storage?"
    )
    parser.add_argument("--topic", default="energy systems")
    args = parser.parse_args()

    ctx = run(question=args.question, topic=args.topic, llm=build_llm())
    answers = ctx.list_artifacts(Answer)
    print("level 2 · two LLM calls → linked patch (evidence + answer + provenance)")
    for a in answers:
        print(f"  answer: {a.data.text}")
        linked = ctx.related(a.id, relation="supported_by")
        for ev in linked:
            docs = ctx.related(ev.id, relation="extracted_from")
            print(
                f"  evidence ← {ev.data.source}  (extracted_from={[d.id for d in docs]})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
