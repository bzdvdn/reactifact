"""Per-source document state for incremental ingestion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_VERSION = 2
_RUN_HISTORY = 10


@dataclass(frozen=True)
class DocEntry:
    """Stored fingerprints of one document from the last successful run."""

    content: str
    listing: str | None = None


@dataclass
class RunRecord:
    """Outcome of one pipeline run, kept per source for visibility."""

    at: str
    ok: bool
    error: str = ""
    duration_seconds: float = 0.0
    documents: int = 0
    chunks: int = 0
    changed: int = 0
    skipped: int = 0
    deleted: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0

    @staticmethod
    def from_dict(value: dict[str, Any]) -> RunRecord:
        return RunRecord(
            at=str(value.get("at", "")),
            ok=bool(value.get("ok", False)),
            error=str(value.get("error", "")),
            duration_seconds=float(value.get("duration_seconds", 0.0)),
            documents=int(value.get("documents", 0)),
            chunks=int(value.get("chunks", 0)),
            changed=int(value.get("changed", 0)),
            skipped=int(value.get("skipped", 0)),
            deleted=int(value.get("deleted", 0)),
            embed_cache_hits=int(value.get("embed_cache_hits", 0)),
            embed_cache_misses=int(value.get("embed_cache_misses", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "ok": self.ok,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "documents": self.documents,
            "chunks": self.chunks,
            "changed": self.changed,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "embed_cache_hits": self.embed_cache_hits,
            "embed_cache_misses": self.embed_cache_misses,
        }


class DocStore:
    """JSON-backed map of `source_id -> {uri -> DocEntry}`.

    State is keyed by source identity **and** a pipeline signature. Changing
    extraction/chunking/embedding settings invalidates stored fingerprints
    instead of silently serving stale chunks. State is committed only after a
    successful run.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._sources: dict[str, dict[str, Any]] = self._load()
        self._runs: dict[str, list[RunRecord]] = self._load_runs()

    @staticmethod
    def default_path() -> Path:
        return Path(".reactifact") / "knowledge" / "state.json"

    def entry(self, source_id: str, signature: str, uri: str) -> DocEntry | None:
        entry = self._sources.get(source_id)
        if entry is None or entry.get("signature") != signature:
            return None
        documents = entry.get("documents")
        if not isinstance(documents, dict):
            return None
        value = documents.get(uri)
        if value is None:
            return None
        return _decode_entry(value)

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, DocEntry],
    ) -> int:
        previous = self._sources.get(source_id, {}).get("documents", {})
        if not isinstance(previous, dict):
            previous = {}
        deleted = sum(1 for uri in previous if uri not in current)
        self._sources[source_id] = {
            "signature": signature,
            "documents": {
                uri: {
                    "content": entry.content,
                    "listing": entry.listing,
                }
                for uri, entry in current.items()
            },
        }
        return deleted

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {
                    "version": _VERSION,
                    "sources": self._sources,
                    "runs": {
                        source_id: [run.to_dict() for run in history]
                        for source_id, history in self._runs.items()
                    },
                },
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)

    def record_run(self, source_id: str, record: RunRecord) -> None:
        history = self._runs.setdefault(source_id, [])
        history.insert(0, record)
        del history[_RUN_HISTORY:]
        self.save()

    def runs(self, source_id: str) -> list[RunRecord]:
        return list(self._runs.get(source_id, []))

    def last_run(self, source_id: str) -> RunRecord | None:
        history = self._runs.get(source_id)
        return history[0] if history else None

    def sources(self) -> list[tuple[str, dict[str, Any]]]:
        return sorted(self._sources.items())

    def snapshot(self, *, history: int = _RUN_HISTORY) -> list[dict[str, Any]]:
        """Per-source status plus recent runs, ready for a JSON endpoint/UI."""
        out: list[dict[str, Any]] = []
        for source_id in sorted(set(self._sources) | set(self._runs)):
            runs = self.runs(source_id)[:history]
            out.append(
                {
                    "source_id": source_id,
                    "last_run": runs[0].to_dict() if runs else None,
                    "runs": [run.to_dict() for run in runs],
                }
            )
        return out

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        sources = data.get("sources")
        if not isinstance(sources, dict):
            return {}
        loaded: dict[str, dict[str, Any]] = {}
        for source_id, entry in sources.items():
            if isinstance(entry, dict):
                loaded[str(source_id)] = entry
        return loaded

    def _load_runs(self) -> dict[str, list[RunRecord]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        runs = data.get("runs")
        if not isinstance(runs, dict):
            return {}
        loaded: dict[str, list[RunRecord]] = {}
        for source_id, history in runs.items():
            if not isinstance(history, list):
                continue
            records = [
                RunRecord.from_dict(record)
                for record in history
                if isinstance(record, dict)
            ]
            if records:
                loaded[str(source_id)] = records
        return loaded


def _decode_entry(value: object) -> DocEntry:
    if isinstance(value, str):
        return DocEntry(content=value)
    if isinstance(value, dict):
        content = value.get("content")
        listing = value.get("listing")
        return DocEntry(
            content=str(content) if content is not None else "",
            listing=str(listing) if isinstance(listing, str) else None,
        )
    return DocEntry(content=str(value))


__all__ = ["DocEntry", "DocStore", "RunRecord"]
