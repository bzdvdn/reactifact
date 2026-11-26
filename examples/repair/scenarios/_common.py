"""Shared fixtures for the repair scenarios — resources, fixture paths, and
the stub LLMs used by the fault-injection / record-replay scenarios."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from reactifact.providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk
from reactifact.resources import RuntimeResources

from ..services.catalog import Catalog
from ..services.images import ImageRenderSettings

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "price.csv"
#: Committed once `collect_stage_against_the_model` is recorded — a small
#: JSONL "cassette" of the real model's request/response pairs (§55), kept
#: next to the scenarios themselves rather than under `data/` (that dir is
#: the app's own price catalog, not test fixtures).
FIXTURE = Path(__file__).resolve().parent / "data" / "collect.jsonl"


def resources(llm: LLMProvider | None = None) -> RuntimeResources:
    res = RuntimeResources(llm=llm)
    res.register(Catalog, Catalog(CATALOG_PATH))
    res.register(ImageRenderSettings, ImageRenderSettings(), owned=True)
    return res


def load_recorded_model(path: Path) -> str:
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    return str(json.loads(first_line)["model"])


class ModelStub(LLMProvider):
    """Carries only the `.model` string `ReplayLLM` needs to key its cache —
    never actually called in `mode='replay'`."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise AssertionError("replay must not fall through to a live call")

    async def stream(  # pragma: no cover
        self, request: LLMRequest
    ) -> AsyncIterator[LLMResponseChunk]:
        for _ in ():
            yield _
        raise AssertionError("replay must not fall through to a live call")
