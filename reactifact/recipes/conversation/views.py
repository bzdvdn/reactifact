"""Read-only transcript views over typed conversation artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ...artifacts import Artifact
from ...context import Context
from ...providers import Message
from ._common import _windowed
from .models import Role


@dataclass(frozen=True)
class MessageSpec:
    """How one artifact type maps into a `Transcript`.

    `render` turns the artifact's data into message text (default: its `.text`
    field, if any). Use it for an assistant type whose payload isn't plain text
    (e.g. a contract JSON rendered to prose).
    """

    type: type[BaseModel]
    role: Role
    render: Callable[[Any], str] | None = None
    #: Extra fields for `Transcript.state()` (e.g. generated image paths).
    meta: Callable[[Any], dict[str, Any]] | None = None
    #: Artifact-level fields for `state()` (id, query_id, sources, …).
    artifact_meta: Callable[[Artifact[Any]], dict[str, Any]] | None = None
    #: The payload key for rendered text (`content` for SPA-shaped history).
    state_field: str = "text"

    def text_of(self, data: Any) -> str:
        if self.render is not None:
            return self.render(data)
        text = getattr(data, "text", None)
        return text if isinstance(text, str) else str(data)


@dataclass(frozen=True)
class TranscriptEntry:
    """One artifact in a `Transcript`, paired with the spec that renders it."""

    spec: MessageSpec
    artifact: Artifact[Any]


@dataclass
class Transcript:
    """A read-only view of a conversation built from *several* artifact types.

    Where `Conversation` owns one message model you append to, `Transcript`
    renders an app's existing typed turns — e.g. `Question` (user) +
    `FinalResponse` (assistant) — into provider `Message`s, leaving the
    artifacts untouched. It removes the merge/sort/window/map boilerplate apps
    otherwise repeat (a role per type, a renderer for a non-text assistant
    side), and its `state` is a `Chat(session_state=…)` payload.
    Messages use the Context's insertion order by default (the order replay
    restores); pass `order_key=` to sort by a field instead.
    """

    specs: list[MessageSpec]
    order_key: Callable[[Artifact[Any]], Any] | None = None

    @property
    def types(self) -> tuple[type[BaseModel], ...]:
        """The distinct artifact types this view reads, in declaration order."""
        return tuple(dict.fromkeys(spec.type for spec in self.specs))

    def entries(self, ctx: Context) -> list[TranscriptEntry]:
        """The typed turns in conversation order, each with its `MessageSpec`."""
        ranked: list[tuple[Any, TranscriptEntry]] = []
        if self.order_key is not None:
            ranked = [
                (self.order_key(a), TranscriptEntry(spec, a))
                for spec in self.specs
                for a in ctx.list_artifacts(spec.type)
            ]
        else:
            # Context insertion order is the replayed conversation order: it is
            # what `Context.from_dict`/append-log replay restore, even when a
            # replay recreates an artifact's timestamp.
            rank = {
                artifact.id: index
                for index, artifact in enumerate(ctx.list_artifacts())
            }
            ranked = [
                (rank[a.id], TranscriptEntry(spec, a))
                for spec in self.specs
                for a in ctx.list_artifacts(spec.type)
            ]
        ranked.sort(key=lambda item: item[0])
        return [entry for _, entry in ranked]

    def messages(
        self, ctx: Context, *, window: int | None = None, include_system: bool = True
    ) -> list[Message]:
        """The turns as provider `Message`s, oldest first (`window` keeps the
        last N)."""
        entries = self.entries(ctx)
        if not include_system:
            entries = [entry for entry in entries if entry.spec.role != "system"]
        entries = _windowed(entries, window)
        return [
            Message(
                role=entry.spec.role, content=entry.spec.text_of(entry.artifact.data)
            )
            for entry in entries
        ]

    def render(
        self,
        artifacts: list[Artifact[Any]],
        *,
        role_labels: Mapping[str, str] | None = None,
    ) -> str:
        """Render a subset of artifacts as `role: text` lines.

        Used by memory/digest callbacks that receive only the stale artifacts,
        not the whole context. `role_labels` renames `user`/`assistant` for a
        localized prompt.
        """
        by_type = {spec.type: spec for spec in self.specs}
        lines: list[str] = []
        for artifact in artifacts:
            spec = by_type.get(type(artifact.data))
            if spec is None:
                continue
            role = (role_labels or {}).get(spec.role, spec.role)
            lines.append(f"{role}: {spec.text_of(artifact.data)}")
        return "\n".join(lines)

    def state(self, ctx: Context) -> dict[str, Any]:
        """`Chat(session_state=transcript.state)` history payload."""
        messages: list[dict[str, Any]] = []
        for entry in self.entries(ctx):
            spec = entry.spec
            artifact = entry.artifact
            message = {
                "role": spec.role,
                spec.state_field: spec.text_of(artifact.data),
                "at": artifact.created_at.isoformat(),
            }
            if spec.artifact_meta is not None:
                message.update(spec.artifact_meta(artifact))
            if spec.meta is not None:
                message.update(spec.meta(artifact.data))
            messages.append(message)
        return {"messages": messages}
