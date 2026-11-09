from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .types import migrate_payload, resolve, type_id_of

TData = TypeVar("TData", bound=BaseModel)
ArtifactType = type[BaseModel]


def artifact_content_hash(data: BaseModel) -> str:
    """sha256 over an artifact payload's canonical JSON (content addressing)."""
    payload = json.dumps(
        data.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactVersion:
    """One immutable artifact revision, addressed by content hash.

    `Artifact`/`Context` keep their mutable convenience API for now, but every
    revision is readable through this immutable record: the commit that wrote
    it, its sha256 and the payload at that exact version.
    """

    artifact_id: str
    version: int
    data_type: str
    data: BaseModel
    content_hash: str
    commit_id: str | None = None
    created_at: datetime | None = None


def compute_dict_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Simple dictionary comparison: returns changed, added and removed keys."""
    diff: dict[str, Any] = {"changed": {}, "added": {}, "removed": {}}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        if key not in old:
            diff["added"][key] = new[key]
        elif key not in new:
            diff["removed"][key] = old[key]
        elif old[key] != new[key]:
            # If both values are dictionaries, compare them recursively
            if isinstance(old[key], dict) and isinstance(new[key], dict):
                nested = compute_dict_diff(old[key], new[key])
                if any(nested.values()):
                    diff["changed"][key] = nested
            else:
                diff["changed"][key] = {"old": old[key], "new": new[key]}
    return diff


class Artifact(Generic[TData]):
    """Wrapper around a Pydantic model with versioning."""

    def __init__(
        self,
        data: TData,
        id: str | None = None,
        created_by_commit: str | None = None,
    ):
        self.id = id or str(uuid.uuid4())
        self.data = data
        # Stable type id (reactifact.types): a registered `type_id`/`TYPE_ID`
        # classvar, else the qualified name — so a rename/move can be tolerated
        # via aliases instead of breaking a persisted session/replay.
        self.data_type = type_id_of(type(data))
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at
        self.created_by_commit = created_by_commit
        self._history: list[
            TData
        ] = []  # previous data versions (excluding the current one)
        #: Absolute version counter — bumped by `update()`, independent of
        #: `len(_history)`. `compact(keep_versions=…)` trims `_history` but must
        #: not change `version` (it is part of `context_hash` and of commit
        #: `Write`s), so it is tracked separately.
        self._version = 0
        # Memoized to_dict(), keyed by version: re-derived on every save()
        # otherwise (session persistence saves after every commit, §reactifact.session)
        # even though most artifacts in a large context are unchanged since the
        # last save — see reactifact/session.py for the calling context.
        self._dict_cache: tuple[int, dict[str, Any]] | None = None

    def update(self, new_data: TData) -> None:
        """Saves the current version to history and replaces the data."""
        self._history.append(self.data)
        self.data = new_data
        self._version += 1
        self.updated_at = datetime.now(UTC)

    def trim_history(self, keep: int) -> None:
        """Keeps only the last `keep` previous versions (0 drops them all).

        `version` (and so `context_hash`) is unaffected — only the ability to
        inspect older versions via `history`/`get_all_versions`/`diff` is
        bounded. Used by `Context.compact`.
        """
        if keep < 0:
            raise ValueError("keep must be >= 0")
        if len(self._history) > keep:
            self._history = self._history[-keep:] if keep else []
            self._dict_cache = None

    @property
    def history(self) -> list[TData]:
        """Returns a copy of the list of previous versions (excluding the current one)."""
        return list(self._history)

    @property
    def version(self) -> int:
        """Current version (0 – original, 1 – after the first update, etc.)"""
        return self._version

    def get_all_versions(self) -> list[TData]:
        """Returns all data versions, including the current one, from oldest to newest."""
        return self._history + [self.data]

    def at(self, version: int) -> TData:
        """Read one historical version immutably (0 = original, current = `version`).

        Unlike `get_all_versions()`, `version` is the artifact's absolute
        version counter, not a list index. Raises when that version was trimmed
        by `Context.compact(keep_versions=...)`.
        """
        if version == self._version:
            return self.data
        offset = version - (self._version - len(self._history))
        if version < 0 or offset < 0 or offset >= len(self._history):
            raise ValueError(
                f"version {version} is not available for artifact {self.id!r} "
                f"(current={self._version}, retained={len(self._history) + 1})"
            )
        return self._history[offset]

    def diff(self, old_version: int, new_version: int) -> dict[str, Any]:
        """Returns a diff between two versions by their numbers (0 – the oldest)."""
        versions = self.get_all_versions()
        if old_version < 0 or new_version >= len(versions) or old_version > new_version:
            raise ValueError(
                f"Invalid version indices: old={old_version}, new={new_version}"
            )
        old_data = versions[old_version].model_dump()
        new_data = versions[new_version].model_dump()
        return compute_dict_diff(old_data, new_data)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the artifact, including its full version history.

        Memoized per `version` (bumped by `update()`): an unchanged artifact
        returns the same dict instance on a later call instead of re-walking
        `model_dump()` over its entire history again — the history only grows
        monotonically, so a cache keyed by version can never go stale.
        """
        if self._dict_cache is not None and self._dict_cache[0] == self.version:
            return self._dict_cache[1]
        d = {
            "id": self.id,
            "data_type": self.data_type,
            "data": self.data.model_dump(mode="json"),
            "version": self._version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "history": [v.model_dump(mode="json") for v in self._history],
            "created_by_commit": self.created_by_commit,
        }
        self._dict_cache = (self.version, d)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Artifact[Any]:
        data_type = d["data_type"]
        model_class = resolve(data_type)
        data: Any = model_class.model_validate(migrate_payload(data_type, d["data"]))
        artifact = cls(
            data=data, id=d["id"], created_by_commit=d.get("created_by_commit")
        )
        artifact.created_at = datetime.fromisoformat(d["created_at"])
        artifact.updated_at = datetime.fromisoformat(d["updated_at"])
        history: list[Any] = [
            model_class.model_validate(migrate_payload(data_type, h))
            for h in d["history"]
        ]
        artifact._history = history
        # Older payloads predate the explicit counter — fall back to history len.
        artifact._version = int(d.get("version", len(artifact._history)))
        return artifact

    def __repr__(self) -> str:
        return f"<Artifact id={self.id!r} type={self.data.__class__.__name__} v{self.version}>"


class ArtifactStore:
    """Append-only, content-addressed artifact versions.

    Phase 1: the store lives beside the mutable `Artifact` API. It is the
    canonical lookup for revisions and content hashes; `_history` remains for
    backward compatibility until the frozen-handle migration.
    """

    def __init__(self) -> None:
        self._current: dict[str, int] = {}
        self._versions: dict[str, list[ArtifactVersion]] = {}
        self._by_content: dict[tuple[str, str], set[tuple[str, int]]] = {}

    def _index(self, record: ArtifactVersion) -> None:
        key = (record.data_type, record.content_hash)
        self._by_content.setdefault(key, set()).add(
            (record.artifact_id, record.version)
        )

    def create(
        self,
        data: BaseModel,
        artifact_id: str,
        *,
        commit_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactVersion:
        if artifact_id in self._current:
            raise ValueError(f"artifact {artifact_id!r} already exists")
        record = ArtifactVersion(
            artifact_id=artifact_id,
            version=0,
            data_type=type_id_of(type(data)),
            data=data,
            content_hash=artifact_content_hash(data),
            commit_id=commit_id,
            created_at=created_at or datetime.now(UTC),
        )
        self._versions[artifact_id] = [record]
        self._current[artifact_id] = 0
        self._index(record)
        return record

    def update(
        self,
        artifact_id: str,
        data: BaseModel,
        *,
        commit_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactVersion | None:
        current = self._current.get(artifact_id)
        if current is None:
            return None
        versions = self._versions[artifact_id]
        last = versions[-1]
        data_type = type_id_of(type(data))
        content_hash = artifact_content_hash(data)
        if last.data_type == data_type and last.content_hash == content_hash:
            return last
        record = ArtifactVersion(
            artifact_id=artifact_id,
            version=last.version + 1,
            data_type=data_type,
            data=data,
            content_hash=content_hash,
            commit_id=commit_id,
            created_at=created_at or datetime.now(UTC),
        )
        versions.append(record)
        self._current[artifact_id] = record.version
        self._index(record)
        return record

    def delete(self, artifact_id: str) -> bool:
        return self._current.pop(artifact_id, None) is not None

    def current_version(self, artifact_id: str) -> int | None:
        return self._current.get(artifact_id)

    def version(self, artifact_id: str, version: int) -> ArtifactVersion | None:
        versions = self._versions.get(artifact_id)
        if not versions:
            return None
        first = versions[0].version
        offset = version - first
        if offset < 0 or offset >= len(versions):
            return None
        return versions[offset]

    def history(self, artifact_id: str) -> list[ArtifactVersion]:
        return list(self._versions.get(artifact_id, ()))

    def find_versions(
        self, content_hash: str, data_type: str | None = None
    ) -> list[ArtifactVersion]:
        found: list[ArtifactVersion] = []
        if data_type is not None:
            keys = [(data_type, content_hash)]
        else:
            keys = [key for key in self._by_content if key[1] == content_hash]
        for key in keys:
            for artifact_id, version in self._by_content.get(key, ()):
                record = self.version(artifact_id, version)
                if record is not None:
                    found.append(record)
        found.sort(key=lambda record: (record.artifact_id, record.version))
        return found

    def bind_commit(self, commit: Any) -> None:
        """Stamps versions written by `commit` with its id (runtime applied them
        before the commit id existed)."""
        for write in commit.writes:
            if write.op_type == "delete":
                continue
            record = self.version(write.artifact_id, write.version)
            if record is None or record.commit_id is not None:
                continue
            replacement = replace(record, commit_id=commit.id)
            versions = self._versions[write.artifact_id]
            versions[versions.index(record)] = replacement

    def trim_history(self, artifact_id: str, keep: int) -> None:
        """Keeps the current version plus the last `keep` previous versions."""
        versions = self._versions.get(artifact_id)
        if not versions:
            return
        current = self._current.get(artifact_id)
        retained = (
            versions[-keep - 1 :]
            if current is not None
            else (versions[-keep:] if keep else [])
        )
        if not retained:
            self._versions[artifact_id] = []
            return
        self._versions[artifact_id] = retained

    @classmethod
    def from_artifacts(cls, artifacts: dict[str, Artifact[Any]]) -> ArtifactStore:
        store = cls()
        for artifact_id, artifact in artifacts.items():
            history = artifact.history
            first_version = artifact.version - len(history)
            records: list[ArtifactVersion] = []
            for offset, data in enumerate([*history, artifact.data]):
                version = first_version + offset
                record = ArtifactVersion(
                    artifact_id=artifact_id,
                    version=version,
                    data_type=type_id_of(type(data)),
                    data=data,
                    content_hash=artifact_content_hash(data),
                    commit_id=(
                        artifact.created_by_commit
                        if version == artifact.version
                        else None
                    ),
                    created_at=(
                        artifact.updated_at
                        if version == artifact.version
                        else artifact.created_at
                    ),
                )
                records.append(record)
                store._index(record)
            store._versions[artifact_id] = records
            store._current[artifact_id] = artifact.version
        return store
