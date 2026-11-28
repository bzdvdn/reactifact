import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Context,
    EventType,
    Patch,
    Runtime,
    RuntimeResources,
    Trigger,
)
from reactifact.providers import FakeEmbedder


class TextData(BaseModel):
    text: str


class EnrichedText(BaseModel):
    text: str
    embedding: list[float]


class TextProcessor(Agent):
    def __init__(self):
        super().__init__(
            "text_processor", triggers=[Trigger(EventType.ARTIFACT_CREATED, TextData)]
        )

    async def run(self, event, context):
        embedder = context.resources.embedder
        if embedder is None:
            return None
        art = context.get(event.artifact_id)
        if art is None:
            return None
        emb = await embedder.embed([art.data.text])
        return Patch().create(EnrichedText(text=art.data.text, embedding=emb[0]))


def test_agent_uses_embedder():
    ws = Context(resources=RuntimeResources(embedder=FakeEmbedder(dim=4)))
    runtime = Runtime(ws, agents=[TextProcessor()])

    ws.create(TextData(text="hello"))
    asyncio.run(runtime.arun())

    enriched = ws.list_artifacts(EnrichedText)
    assert len(enriched) == 1
    assert len(enriched[0].data.embedding) == 4
