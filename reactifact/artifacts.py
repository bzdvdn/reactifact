from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from .types import migrate_payload, resolve, type_id_of

TData = TypeVar("TData", bound=BaseModel)
ArtifactType = type[BaseModel]


def payload_content_hash(payload: dict[str, Any]) -> str:
    """sha256 over a canonical JSON payload (content addressing)."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def artifact_content_hash(data: BaseModel) -> str:
    """sha256 over an artifact payload's canonical JSON (content addressing)."""
    return payload_content_hash(data.model_dump(mode="json"))


def blob_key(data_type: str, content_hash: str) -> str:
    """Key of a payload in the v2 persisted `blobs` map."""
    return f"{data_type}:{content_hash}"


@dataclass(frozen=True)
class ArtifactVersion:
    """One immutable artifact revision, addressed by content hash.

    The commit that wrote it, its sha256 and the payload at that exact version.
    `Artifact` is a frozen handle over one of these records.
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


@dataclass(frozen=True, init=False)
class Artifact(Generic[TData]):
    """An immutable snapshot/handle for one artifact version.

    `data` is a Pydantic payload; `_store` is an internal reference used by the
    deprecated `history`/`at`/`diff` bridges. Mutation goes through `Context`.
    """

    id: str
    data: TData
    data_type: str
    version: int
    content_hash: str
    created_at: datetime
    updated_at: datetime
    created_by_commit: str | None
    _store: ArtifactStore | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        data: TData,
        id: str | None = None,
        created_by_commit: str | None = None,
        *,
        version: int = 0,
        content_hash: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        now = datetime.now(UTC)
        object.__setattr__(self, "id", id or str(uuid.uuid4()))
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "data_type", type_id_of(type(data)))
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self, "content_hash", content_hash or artifact_content_hash(data)
        )
        object.__setattr__(self, "created_at", created_at or now)
        object.__setattr__(self, "updated_at", updated_at or now)
        object.__setattr__(self, "created_by_commit", created_by_commit)
        object.__setattr__(self, "_store", store)

    @property
    def history(self) -> list[TData]:
        """Previous versions (deprecated bridge; use `Context.version_history`)."""
        if self._store is None:
            return []
        return cast(
            list[TData],
            [
                record.data
                for record in self._store.history(self.id)
                if record.version < self.version
            ],
        )

    def get_all_versions(self) -> list[TData]:
        """All retained versions, including the current one (deprecated bridge)."""
        return self.history + [self.data]

    def at(self, version: int) -> TData:
        """One retained version (deprecated bridge; use `Context.artifact_version`)."""
        if version == self.version:
            return self.data
        if self._store is not None:
            record = self._store.version(self.id, version)
            if record is not None:
                return cast(TData, record.data)
        raise ValueError(
            f"version {version} is not available for artifact {self.id!r} "
            f"(current={self.version})"
        )

    def diff(self, old_version: int, new_version: int) -> dict[str, Any]:
        """Diff two retained versions by list index (deprecated bridge)."""
        versions = self.get_all_versions()
        if old_version < 0 or new_version >= len(versions) or old_version > new_version:
            raise ValueError(
                f"Invalid version indices: old={old_version}, new={new_version}"
            )
        return compute_dict_diff(
            versions[old_version].model_dump(), versions[new_version].model_dump()
        )

    def to_dict(self) -> dict[str, Any]:
        """Legacy v1 serialization; `Context.to_dict` still uses it."""
        return {
            "id": self.id,
            "data_type": self.data_type,
            "data": self.data.model_dump(mode="json"),
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "history": [v.model_dump(mode="json") for v in self.history],
            "created_by_commit": self.created_by_commit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Artifact[Any]:
        data_type = d["data_type"]
        model_class = resolve(data_type)
        data: Any = model_class.model_validate(migrate_payload(data_type, d["data"]))
        history: list[Any] = [
            model_class.model_validate(migrate_payload(data_type, h))
            for h in d.get("history", [])
        ]
        version = int(d.get("version", len(history)))
        created_at = datetime.fromisoformat(d["created_at"])
        updated_at = datetime.fromisoformat(d["updated_at"])

        store = ArtifactStore()
        first = version - len(history)
        for offset, payload in enumerate(history):
            record = ArtifactVersion(
                artifact_id=d["id"],
                version=first + offset,
                data_type=data_type,
                data=payload,
                content_hash=artifact_content_hash(payload),
                commit_id=None,
                created_at=created_at,
            )
            store._versions.setdefault(d["id"], []).append(record)
            store._index(record)
        current = ArtifactVersion(
            artifact_id=d["id"],
            version=version,
            data_type=data_type,
            data=data,
            content_hash=artifact_content_hash(data),
            commit_id=d.get("created_by_commit"),
            created_at=updated_at,
        )
        store._versions.setdefault(d["id"], []).append(current)
        store._index(current)
        store._current[d["id"]] = version

        return cls(
            data=data,
            id=d["id"],
            created_by_commit=d.get("created_by_commit"),
            version=version,
            content_hash=current.content_hash,
            created_at=created_at,
            updated_at=updated_at,
            store=store,
        )

    def __repr__(self) -> str:
        return f"<Artifact id={self.id!r} type={self.data.__class__.__name__} v{self.version}>"


class ArtifactStore:
    """Append-only, content-addressed artifact versions.

    The canonical materialized state: current pointer per artifact, every
    retained version, and a content-hash index. `Artifact` handles are frozen
    views over these records; history lives here, not on the artifact.
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

    def current_artifact(self, artifact_id: str) -> Artifact[Any] | None:
        """The current version as an immutable handle."""
        version = self._current.get(artifact_id)
        if version is None:
            return None
        record = self.version(artifact_id, version)
        if record is None:
            return None
        versions = self._versions[artifact_id]
        first = versions[0] if versions else record
        return Artifact(
            data=record.data,
            id=artifact_id,
            created_by_commit=first.commit_id,
            version=record.version,
            content_hash=record.content_hash,
            created_at=first.created_at,
            updated_at=record.created_at,
            store=self,
        )

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

    def copy(self, *, deep: bool = True) -> ArtifactStore:
        """Independent store; version records and commit ids are preserved."""
        store = ArtifactStore()
        for artifact_id, versions in self._versions.items():
            copied: list[ArtifactVersion] = []
            for record in versions:
                data = record.data.model_copy(deep=True) if deep else record.data
                replacement = replace(record, data=data)
                copied.append(replacement)
                store._index(replacement)
            store._versions[artifact_id] = copied
            if artifact_id in self._current:
                store._current[artifact_id] = self._current[artifact_id]
        return store

    def extend(self, other: ArtifactStore, *, ids: set[str] | None = None) -> None:
        """Absorb another store's versions (all, or a selected set of ids)."""
        for artifact_id, versions in other._versions.items():
            if ids is not None and artifact_id not in ids:
                continue
            self._versions[artifact_id] = list(versions)
            for record in versions:
                self._index(record)
            if artifact_id in other._current:
                self._current[artifact_id] = other._current[artifact_id]

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
