"""reactifact.audit — answer provenance and reproducibility (§34, §55, §56).

Two things an auditable agent needs, both offline and dependency-free:

1. `build_report(context, answer)` — a self-contained record of *why* an answer
   exists: the answer's content hash, the provenance chain behind it
   (`supported_by` / `derived_from` / `materialized_from` / …), each artifact's
   version, author and content hash, and the source locators it rests on.
   Exportable as JSON (machine-checkable) or Markdown (human/regulator-readable).

2. `context_hash(context)` — a sha256 over the run's canonical state
   (artifacts + relations + version). Two runs that produce the same state hash
   the same; recording it with a `ReplayLLM` recording turns "is this run
   reproducible?" into a string comparison (`reactifact replay --verify <hash>`,
   and the `fintech_audit` example).

Hashes are sha256 over canonical (sorted, JSON-safe) content, so the *hash*
fields are reproducible: the same state yields the same `context_sha256` and
per-artifact hashes, run to run. The report also carries `created_at` values
for a human reader, so the full JSON is not byte-identical across runs — the
fingerprint is.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .artifacts import Artifact
from .context import Context

#: Relations followed by default when walking an answer's provenance chain.
DEFAULT_RELATIONS: tuple[str, ...] = (
    "supported_by",
    "derived_from",
    "extracted_from",
    "materialized_from",
    "resolved_from",
    "calculated_from",
    "refuted_by",
    "contradicted_by",
)


def _canonical(value: Any) -> str:
    """Canonical JSON for hashing (sorted keys; pydantic models dumped)."""
    if isinstance(value, BaseModel):
        payload: Any = value.model_dump(mode="json")
    else:
        payload = value
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def data_hash(value: Any) -> str:
    """sha256 of a value's canonical JSON — a stable content fingerprint."""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def artifact_hash(artifact: Artifact[Any]) -> str:
    """Content hash of one artifact's data (its type + id are not included)."""
    return data_hash(artifact.data)


def context_hash(context: Context) -> str:
    """sha256 over the run's canonical state: version, artifacts, relations.

    Includes each artifact's id, type, version and content hash, plus every
    relation edge — everything that makes two runs' states the same or
    different. Timestamps are deliberately excluded, so a re-run that reaches
    the same state hashes identically.
    """
    artifacts = [
        {
            "id": artifact.id,
            "type": type(artifact.data).__name__,
            "version": artifact.version,
            "sha256": artifact_hash(artifact),
        }
        for artifact in sorted(context.list_artifacts(), key=lambda a: a.id)
    ]
    relations = sorted(
        (rel.source_id, rel.relation, rel.target_id) for rel in context.relations()
    )
    payload = {
        "version": context.version,
        "artifacts": artifacts,
        "relations": relations,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class ProvenanceEntry(BaseModel):
    """One artifact in an answer's provenance chain, with its content hash."""

    artifact_id: str
    data_type: str
    version: int
    sha256: str
    produced_by: str = ""
    created_at: datetime | None = None


class AuditReport(BaseModel):
    """A reproducible record of an answer and the evidence behind it."""

    session_id: str = ""
    context_version: int
    context_sha256: str
    answer: ProvenanceEntry
    provenance: list[ProvenanceEntry] = []
    relations: list[tuple[str, str, str]] = []
    sources: list[str] = []


def _entry(artifact: Artifact[Any], produced_by: str) -> ProvenanceEntry:
    return ProvenanceEntry(
        artifact_id=artifact.id,
        data_type=type(artifact.data).__name__,
        version=artifact.version,
        sha256=artifact_hash(artifact),
        produced_by=produced_by,
        created_at=artifact.created_at,
    )


def _producers(context: Context) -> dict[str, str]:
    """artifact_id → author of the commit that last wrote it (from history)."""
    producers: dict[str, str] = {}
    for commit in context.history():
        for write in commit.writes:
            producers[write.artifact_id] = commit.author
    return producers


def _resolve_answer(context: Context, answer: Artifact[Any] | str) -> Artifact[Any]:
    if isinstance(answer, Artifact):
        return answer
    artifact = context.get(answer)
    if artifact is None:
        raise KeyError(f"answer artifact {answer!r} not found in context")
    return artifact


def build_report(
    context: Context,
    answer: Artifact[Any] | str,
    *,
    relations: Sequence[str] | None = None,
    session_id: str = "",
) -> AuditReport:
    """Walks the provenance chain behind `answer` into a verifiable report.

    Breadth-first over `context.relations()`, following `relations` (all by
    default), so every artifact that contributed to the answer is included with
    its content hash and producing author. `SourceRef` locators in the chain
    become `sources`.
    """
    followed = set(relations) if relations is not None else set(DEFAULT_RELATIONS)
    answer_artifact = _resolve_answer(context, answer)
    producers = _producers(context)

    entries: list[ProvenanceEntry] = []
    edges: list[tuple[str, str, str]] = []
    sources: list[str] = []
    visited: set[str] = set()
    queue = [answer_artifact]
    while queue:
        artifact = queue.pop(0)
        if artifact.id in visited:
            continue
        visited.add(artifact.id)
        entries.append(_entry(artifact, producers.get(artifact.id, "")))
        locator = getattr(artifact.data, "locator", None)
        if isinstance(locator, str) and locator and locator not in sources:
            sources.append(locator)
        for rel in context.relations(source_id=artifact.id):
            if rel.relation not in followed:
                continue
            edges.append((rel.source_id, rel.relation, rel.target_id))
            target = context.get(rel.target_id)
            if target is not None and target.id not in visited:
                queue.append(target)

    return AuditReport(
        session_id=session_id,
        context_version=context.version,
        context_sha256=context_hash(context),
        answer=entries[0],
        provenance=entries[1:],
        relations=edges,
        sources=sources,
    )


def report_to_json(report: AuditReport, *, indent: int | None = 2) -> str:
    """The report as canonical JSON (sorted keys → reproducible bytes)."""
    return json.dumps(
        report.model_dump(mode="json"),
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
    )


def report_to_markdown(report: AuditReport) -> str:
    """The report as Markdown — for a PR comment, a ticket, or a regulator."""
    lines = [
        "# Audit report",
        "",
        f"- context version: {report.context_version}",
        f"- context sha256: `{report.context_sha256}`",
        f"- session: {report.session_id or '(none)'}",
        "",
        "## Answer",
        "",
        f"- `{report.answer.artifact_id}` ({report.answer.data_type}, "
        f"v{report.answer.version})",
        f"- sha256: `{report.answer.sha256}`",
        f"- produced by: {report.answer.produced_by or '(seed)'}",
        "",
        "## Provenance",
        "",
        "| artifact | type | v | sha256 | produced by |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in [report.answer, *report.provenance]:
        lines.append(
            f"| `{entry.artifact_id}` | {entry.data_type} | {entry.version} | "
            f"`{entry.sha256[:12]}…` | {entry.produced_by or '(seed)'} |"
        )
    if report.relations:
        lines += ["", "## Edges", ""]
        lines += [f"- `{s}` —{r}→ `{t}`" for s, r, t in report.relations]
    if report.sources:
        lines += ["", "## Sources", ""]
        lines += [f"- `{source}`" for source in report.sources]
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_RELATIONS",
    "AuditReport",
    "ProvenanceEntry",
    "artifact_hash",
    "build_report",
    "context_hash",
    "data_hash",
    "report_to_json",
    "report_to_markdown",
]
