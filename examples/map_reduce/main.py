"""map_reduce — fan-out over chunks → summarize → reduce (LangChain-style).

A `Doc` is split deterministically into chunks; each chunk is summarized by a
produce (the runtime fans them out, §24 — several chunk events run in
parallel); a `Combine` produce waits for every chunk, asks the model for a
final summary, and links it back to each chunk's summary. All effects in one
atomic step per produce.

    uv run python -m examples.map_reduce.main
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


CHUNKS = 3


class Doc(BaseModel):
    text: str


class Chunk(BaseModel):
    index: int
    text: str


class ChunkSummary(BaseModel):
    index: int
    text: str


class FinalSummary(BaseModel):
    text: str
    sources: list[str] = []


class _Text(BaseModel):
    text: str


_SUMMARIZE = PromptTemplate(
    """You summarize one document chunk concisely.
Chunk {index}:
{text}"""
)
_COMBINE = PromptTemplate(
    """You combine the {count} chunk summaries into a concise full summary.
{parts}"""
)


def _split(text: str, n: int = CHUNKS) -> list[str]:
    """Even, deterministic split of the document into n chunks."""
    size = max(1, len(text) // n)
    return [text[i * size : (i + 1) * size if i < n - 1 else None] for i in range(n)]


class Split(Produce[Chunk]):
    artifact_type = Chunk

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        doc = next((d for d in context.list_artifacts(Doc)), None)
        if doc is None or context.list_artifacts(Chunk):
            return None  # already split (§42)
        for index, piece in enumerate(_split(doc.data.text)):
            chunk = self.effects.create(
                Chunk(index=index, text=piece), id=f"chunk:{doc.id}:{index}"
            )
            chunk.link("from_doc", doc.id)
        return None


class Summarize(Produce[ChunkSummary]):
    artifact_type = ChunkSummary
    # Flow also consumes Doc and ChunkSummary — reacts_to keeps this produce
    # from re-running on those events too, moving the old
    # `isinstance(chunk.data, Chunk)` guard to the declaration; declaring
    # `trigger` below gets the resolved Chunk directly, guaranteed non-None.
    reacts_to = (Chunk,)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        chunk = call.trigger
        assert chunk is not None
        if context.get(f"summary:{chunk.id}") is not None:
            return None
        body = await structured_llm(
            context,
            schema=_Text,
            system=_SUMMARIZE.render(index=chunk.data.index, text=chunk.data.text),
            user=chunk.data.text,
        )
        text = (
            body.text
            if body is not None
            else f"(no summary — not configured or call failed) {chunk.data.text[:120]}"
        )
        s = self.effects.create(
            ChunkSummary(index=chunk.data.index, text=text),
            id=f"summary:{chunk.id}",
        )
        s.link("derived_from", chunk)
        return None


class Combine(Produce[FinalSummary]):
    artifact_type = FinalSummary

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        if context.list_artifacts(FinalSummary):
            return None
        doc = next((d for d in context.list_artifacts(Doc)), None)
        if doc is None:
            return None
        chunks = sorted(context.list_artifacts(Chunk), key=lambda c: c.data.index)
        if not chunks:
            return None  # not split yet (§69: eligibility is a state decision)
        summaries = {}
        for c in chunks:
            s = context.get(f"summary:{c.id}")
            if s is not None:
                summaries[c.data.index] = s
        if len(summaries) < len(chunks):
            return None  # wait for every chunk's summary (§24)
        parts = "\n".join(f"[{i}] {summaries[i].data.text}" for i in sorted(summaries))
        body = await structured_llm(
            context,
            schema=_Text,
            system=_COMBINE.render(count=len(chunks), parts=parts),
            user=parts,
        )
        text = body.text if body is not None else f"(offline combine) {parts[:200]}"
        final = self.effects.create(
            FinalSummary(
                text=text,
                sources=[f"chunk:{doc.id}:{i}" for i in sorted(summaries)],
            ),
            id=f"final:{doc.id}",
        )
        for i in sorted(summaries):
            final.link("supported_by", summaries[i])
        return None


class Flow(Agent):
    name = "map_reduce"
    consumes = [Consume(Doc), Consume(Chunk), Consume(ChunkSummary)]
    produces = [Split(), Summarize(), Combine()]


DEFAULT_SOURCE = (
    "Reactifact treats an agent system as versioned artifacts and reactions, "
    "not a hand-drawn execution graph wired node by node.\n\n"
    "Every agent declares only what it consumes and produces; the runtime "
    "works out execution order from state changes as they happen.\n\n"
    "Each derived artifact links back to what produced it, so explaining "
    "why the system answered a certain way is a graph walk, not a guess."
)


def run(
    *,
    source: str = DEFAULT_SOURCE,
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Doc(text=source))
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.map_reduce.main")
    parser.parse_args()

    ctx = run(llm=build_llm())
    finals = ctx.list_artifacts(FinalSummary)
    print("map_reduce · split → summarize (fan-out) → combine")
    for f in finals:
        print(f"  final ({len(f.data.sources)} chunks):")
        print(f"    {f.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
