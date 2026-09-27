from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

TData = TypeVar("TData", bound=BaseModel)
ArtifactType = type[BaseModel]


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


def _import_class(full_name: str) -> Any:
    module_name, class_name = full_name.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


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
        self.data_type = f"{type(data).__module__}.{type(data).__qualname__}"
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
        model_class = _import_class(d["data_type"])
        data = model_class.model_validate(d["data"])
        artifact = cls(
            data=data, id=d["id"], created_by_commit=d.get("created_by_commit")
        )
        artifact.created_at = datetime.fromisoformat(d["created_at"])
        artifact.updated_at = datetime.fromisoformat(d["updated_at"])
        artifact._history = [model_class.model_validate(h) for h in d["history"]]
        # Older payloads predate the explicit counter — fall back to history len.
        artifact._version = int(d.get("version", len(artifact._history)))
        return artifact

    def __repr__(self) -> str:
        return f"<Artifact id={self.id!r} type={self.data.__class__.__name__} v{self.version}>"
