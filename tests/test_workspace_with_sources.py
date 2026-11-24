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
from reactifact.sources import Source, SourceRef


class MySource(Source):
    def __init__(self):
        super().__init__("mysource")

    async def resolve(self, ref: SourceRef) -> str:
        return f"resolved:{ref.locator}"


class Doc(BaseModel):
    content: str


class RefToDoc(Agent):
    def __init__(self):
        super().__init__(
            "ref_to_doc", triggers=[Trigger(EventType.ARTIFACT_CREATED, SourceRef)]
        )

    async def run(self, event, context):
        ref = context.get(event.artifact_id)
        if not ref:
            return None
        data = ref.data
        src = context.resources.get_source(data.source_id)
        if not src:
            return None
        content = await src.resolve(data)
        return Patch().create(Doc(content=content))


def test_resolver_group():
    ws = Context(resources=RuntimeResources(sources={"mysource": MySource()}))
    runtime = Runtime(ws, agents=[RefToDoc()])
    ws.create(SourceRef(source_id="mysource", locator="abc"))
    asyncio.run(runtime.arun())
    docs = ws.list_artifacts(Doc)
    assert len(docs) == 1
    assert docs[0].data.content == "resolved:abc"
