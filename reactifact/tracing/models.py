"""Trace models (§54).

`RunTrace` is a run: metadata + agent spans. Each `AgentSpan` carries
`reads`/`writes` as `ArtifactRef` (with type and truncated content) and a list
of `LLMCall` (prompt/response/tokens). `RunTrace.llm_calls` is a flat projection
of all run model calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    """Reference to an artifact with data for inspection (§54).

    `data` is truncated JSON (string), so as not to drag the entire content into the trace.
    """

    artifact_id: str
    version: int = 0
    op_type: str = ""
    data_type: str = ""
    data: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class LLMCall(BaseModel):
    """One model call: request, response, tokens, latency (§54)."""

    agent: str = ""
    provider: str = ""
    model: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    response: str = ""
    #: Fingerprint of the prompt template behind this call, if the caller set
    #: `LLMRequest.prompt_hash` (`PromptTemplate.hash`). Empty when unset.
    prompt_hash: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None


class RelationRef(BaseModel):
    """A provenance edge recorded for a span (`source —relation→ target`, §34)."""

    source_id: str
    relation: str
    target_id: str
    source_type: str = ""
    target_type: str = ""


class Tag(BaseModel):
    """A vocabulary entry: a named label humans use to mark runs for review.

    Tags are **annotations**, not runtime data: the runtime never produces them,
    a reviewer adds them from the dashboard after inspecting a trace (e.g.
    `bad-prompt`, `hallucination`, `needs-review`). `count` is filled in by
    `list_tags` (how many runs carry the tag) and left at 0 elsewhere.
    """

    name: str
    color: str = ""
    count: int = 0
    created_at: datetime | None = None


class TagAssignment(BaseModel):
    """One tag attached to one run, with an optional reviewer note.

    The note is free text describing *what is wrong* / *what to change* — the
    payload a reviewer leaves so the run can be found again by tag and acted on
    (prompt/behaviour fix). `color` is copied from the vocabulary for rendering.
    """

    tag: str
    note: str = ""
    color: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentSpan(BaseModel):
    """A single agent execution within a run (§54)."""

    agent: str
    event_type: str = ""
    reads: list[ArtifactRef] = Field(default_factory=list)
    writes: list[ArtifactRef] = Field(default_factory=list)
    relations: list[RelationRef] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None
    started_at: datetime | None = None


class RunTrace(BaseModel):
    """Trace of a single run (turn): agent executions + outcome."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    outcome: str = ""
    spans: list[AgentSpan] = Field(default_factory=list)
    #: Reviewer annotations attached *after* the run (UI review workflow, §54).
    #: The runtime always leaves this empty; `TraceStore.get` populates it.
    annotations: list[TagAssignment] = Field(default_factory=list)

    @property
    def llm_calls(self) -> list[LLMCall]:
        calls: list[LLMCall] = []
        for span in self.spans:
            calls.extend(span.llm_calls)
        return calls

    def add_span(self, span: AgentSpan) -> None:
        self.spans.append(span)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
