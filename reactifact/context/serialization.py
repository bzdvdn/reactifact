"""Context serialization: persisted envelope, migrations and step cursors."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypedDict

from ..artifacts import (
    ArtifactVersion,
    blob_key,
    payload_content_hash,
)
from ..commit_log import CommitLog
from ..events import Event
from ..relations import RelationGraph
from ..types import migrate_payload, resolve, type_id_of
from .index import recompute_stale, reindex_by_type

#: Current persisted-envelope version. Bump when `to_dict`'s shape changes and
#: register a migration in `_CONTEXT_MIGRATIONS` for the previous version.
CONTEXT_FORMAT_VERSION = 2


class SerializedContext(TypedDict, total=False):
    """The persisted Context envelope (`Context.to_dict`).

    `total=False` because older payloads predate some keys (notably
    `format_version`, treated as v1 by `migrate_context_payload`).
    """

    format_version: int
    version: int
    head_id: str | None
    artifacts: dict[str, dict[str, Any]]
    blobs: dict[str, dict[str, Any]]
    relations: list[dict[str, Any]]
    commits: list[dict[str, Any]]
    commit_baseline: dict[str, Any] | None
    events: list[dict[str, Any]]
    fork_name: str
    base: SerializedContext | None
    step_cursors: dict[str, dict[str, dict[str, int]]]


def _v1_artifacts_to_v2(
    artifacts: dict[str, dict[str, Any]],
    blobs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    converted: dict[str, dict[str, Any]] = {}
    for artifact_id, artifact in artifacts.items():
        data_type = artifact["data_type"]
        history = list(artifact.get("history", []))
        version = int(artifact.get("version", len(history)))
        first = version - len(history)
        versions: list[dict[str, Any]] = []
        for offset, payload in enumerate([*history, artifact["data"]]):
            content_version = first + offset
            content_hash = payload_content_hash(payload)
            blobs[blob_key(data_type, content_hash)] = payload
            versions.append(
                {
                    "version": content_version,
                    "data_type": data_type,
                    "content_hash": content_hash,
                    "commit_id": (
                        artifact.get("created_by_commit")
                        if content_version == version
                        else None
                    ),
                    "created_at": (
                        artifact.get("updated_at")
                        if content_version == version
                        else artifact.get("created_at")
                    ),
                }
            )
        converted[artifact_id] = {"current": version, "versions": versions}
    return converted


def _v1_commits_to_v2(
    commits: list[dict[str, Any]],
    blobs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for commit in commits:
        commit = dict(commit)
        operations: list[dict[str, Any]] = []
        for operation in commit.get("operations", []):
            operation = dict(operation)
            if operation.get("type") in {"create", "update"} and "data" in operation:
                data_type = operation["data_type"]
                payload = operation.pop("data")
                operation.pop("data_type", None)
                content_hash = payload_content_hash(payload)
                blobs[blob_key(data_type, content_hash)] = payload
                operation["data_ref"] = {
                    "type": data_type,
                    "hash": content_hash,
                }
            operations.append(operation)
        commit["operations"] = operations
        converted.append(commit)
    return converted


def _migrate_1_to_2(data: dict[str, Any]) -> dict[str, Any]:
    """v1 (inline artifact history + inline commit payloads) -> v2 blobs."""
    blobs: dict[str, dict[str, Any]] = {}
    data = dict(data)
    data["artifacts"] = _v1_artifacts_to_v2(data.get("artifacts", {}), blobs)
    data["commits"] = _v1_commits_to_v2(data.get("commits", []), blobs)
    baseline = data.get("commit_baseline")
    if baseline is not None:
        baseline = dict(baseline)
        baseline["artifacts"] = _v1_artifacts_to_v2(
            baseline.get("artifacts", {}), blobs
        )
        data["commit_baseline"] = baseline
    if isinstance(data.get("base"), dict):
        data["base"] = _migrate_1_to_2(data["base"])
    data["blobs"] = blobs
    return data


#: from_version -> migration that upgrades the payload by one version.
_CONTEXT_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _migrate_1_to_2,
}


def migrate_context_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrades a persisted Context payload to `CONTEXT_FORMAT_VERSION`.

    Payloads written before the version field existed are treated as version 1.
    A payload from a *newer* framework is rejected explicitly rather than
    silently mis-read. Migrations are chained one version at a time.
    """
    version = int(data.get("format_version", 1))
    if version > CONTEXT_FORMAT_VERSION:
        raise ValueError(
            f"context payload format {version} is newer than this reactifact "
            f"supports ({CONTEXT_FORMAT_VERSION}); upgrade reactifact"
        )
    while version < CONTEXT_FORMAT_VERSION:
        migration = _CONTEXT_MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(
                f"no migration registered for context payload format {version}"
            )
        data = migration(data)
        version += 1
        data["format_version"] = version
    return data


def _serialize_step_cursors(
    cursors: Mapping[Any, Mapping[type, Mapping[str, int]]],
) -> dict[str, dict[str, dict[str, int]]]:
    payload: dict[str, dict[str, dict[str, int]]] = {}
    for (agent, step, scope), cursor in cursors.items():
        key = json.dumps([agent, step, scope], ensure_ascii=False, default=str)
        payload[key] = {
            type_id_of(artifact_type): dict(vector)
            for artifact_type, vector in cursor.items()
        }
    return payload


def _deserialize_step_cursors(
    payload: Mapping[str, Mapping[str, Any]],
) -> dict[Any, dict[type, dict[str, int]]]:
    cursors: dict[Any, dict[type, dict[str, int]]] = {}
    for key, values in payload.items():
        try:
            agent, step, scope = json.loads(key)
        except (TypeError, ValueError):
            continue
        cursor: dict[type, dict[str, int]] = {}
        for type_id, raw in values.items():
            try:
                artifact_type = resolve(type_id)
            except Exception:  # noqa: BLE001 — an unknown type is skipped
                continue
            if isinstance(raw, Mapping):
                vector = {str(aid): int(ver) for aid, ver in raw.items()}
            elif isinstance(raw, Sequence) and len(raw) == 2:
                vector = {str(raw[0]): int(raw[1])}
            else:
                continue
            cursor[artifact_type] = vector
        cursors[(agent, step, scope)] = cursor
    return cursors


def serialize_context(context: Any) -> SerializedContext:
    blobs: dict[str, dict[str, Any]] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact_id, versions in context._store._versions.items():
        entries: list[dict[str, Any]] = []
        for record in versions:
            payload = record.data.model_dump(mode="json")
            blobs[blob_key(record.data_type, record.content_hash)] = payload
            entries.append(
                {
                    "version": record.version,
                    "data_type": record.data_type,
                    "content_hash": record.content_hash,
                    "commit_id": record.commit_id,
                    "created_at": (
                        record.created_at.isoformat()
                        if record.created_at is not None
                        else None
                    ),
                }
            )
        artifacts[artifact_id] = {
            "current": context._store.current_version(artifact_id),
            "versions": entries,
        }
    return {
        "format_version": CONTEXT_FORMAT_VERSION,
        "version": context._log.version,
        "head_id": context._log.head_id,
        "artifacts": artifacts,
        "blobs": blobs,
        "relations": context._relations.to_dict(),
        "commits": context._log.to_dict(blobs),
        "commit_baseline": context._log.to_baseline_dict(blobs),
        "events": [event.to_dict() for event in context._events],
        "fork_name": context._fork_name,
        "base": context._base.to_dict() if context._base is not None else None,
        "step_cursors": _serialize_step_cursors(context._step_cursors),
    }


def deserialize_context(cls: type[Any], d: dict[str, Any]) -> Any:
    data = migrate_context_payload(dict(d))
    blobs: dict[str, dict[str, Any]] = data.get("blobs", {})
    ws = cls()
    for artifact_id, info in data.get("artifacts", {}).items():
        records: list[ArtifactVersion] = []
        for version in info.get("versions", []):
            data_type = version["data_type"]
            content_hash = version["content_hash"]
            payload = blobs[blob_key(data_type, content_hash)]
            model_class = resolve(data_type)
            payload_data = model_class.model_validate(
                migrate_payload(data_type, payload)
            )
            record = ArtifactVersion(
                artifact_id=artifact_id,
                version=int(version["version"]),
                data_type=data_type,
                data=payload_data,
                content_hash=content_hash,
                commit_id=version.get("commit_id"),
                created_at=(
                    datetime.fromisoformat(version["created_at"])
                    if version.get("created_at")
                    else None
                ),
            )
            records.append(record)
            ws._store._index(record)
        ws._store._versions[artifact_id] = records
        current = info.get("current")
        if current is not None:
            ws._store._current[artifact_id] = int(current)
    ws._artifacts = {}
    for artifact_id in ws._store._current:
        current = ws._store.current_artifact(artifact_id)
        if current is not None:
            ws._artifacts[artifact_id] = current
    ws._relations = RelationGraph.from_dict(data.get("relations", []))
    ws._log = CommitLog.from_dict(
        data["commits"],
        version=data.get("version"),
        head_id=data.get("head_id"),
        baseline=data.get("commit_baseline"),
        blobs=blobs,
    )
    ws._events = [Event.from_dict(e) for e in data.get("events", [])]
    ws._fork_name = data.get("fork_name", "")
    ws._base = (
        deserialize_context(cls, data["base"]) if data.get("base") is not None else None
    )
    ws._step_cursors = _deserialize_step_cursors(data.get("step_cursors", {}))
    recompute_stale(ws)
    reindex_by_type(ws)
    ws.mark_persisted(ws.version)
    return ws


__all__ = [
    "CONTEXT_FORMAT_VERSION",
    "SerializedContext",
    "migrate_context_payload",
    "serialize_context",
    "deserialize_context",
]
