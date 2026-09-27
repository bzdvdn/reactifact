"""reactifact.commit_log — the git-like commit chain (§12).

Extracted out of `Context`: `CommitLog` owns the ordered list of applied
`Commit`s, the current version/head, and the pure "replay commits into a
state snapshot" logic (`replay_state`/`replay_relations`) that `checkout`,
`diff`, and staleness detection all build on. It knows nothing about the
live artifact/relation stores — `Context` still owns those and asks the log
to replay itself when it needs a past or reconstructed state.
"""

from __future__ import annotations

import copy
from typing import Any

from .artifacts import Artifact
from .commit import Commit
from .patches import Create, Delete, Link, Relation, Unlink, Update
from .relations import RelationKey


class CommitLog:
    """The applied-commit chain: append, replay, roll back (§12).

    Alongside the commit list, maintains two incrementally-updated indices so
    `producing_commit`/`dependents_of` stay O(1)/O(dependents) instead of
    scanning every commit on every call (the two hot paths behind
    `Context.stale_artifacts()`/`_dependents_of()`):

    - `_last_write_commit`: artifact_id → the commit that last wrote it.
    - `_dependents`: artifact_id → the set of artifact ids whose *current*
      producing commit reads it (i.e. "who depends on me right now").
      `_producing_read_ids` tracks each artifact's current read-set so a
      later rewrite can remove its stale edges before adding the new ones.
    """

    __slots__ = (
        "_commits",
        "_version",
        "_head_id",
        "_last_write_commit",
        "_dependents",
        "_producing_read_ids",
        "_dict_cache",
        "_baseline",
        "_baseline_relations",
        "_baseline_version",
        "_baseline_head_id",
    )

    def __init__(self) -> None:
        self._commits: list[Commit] = []
        self._version: int = 0
        self._head_id: str | None = None
        self._last_write_commit: dict[str, Commit] = {}
        self._dependents: dict[str, set[str]] = {}
        self._producing_read_ids: dict[str, set[str]] = {}
        # Memoized Commit.to_dict(), keyed by commit id: a commit is immutable
        # once appended (writes/reads/operations never change afterward), so
        # this can never go stale — see `to_dict()`.
        self._dict_cache: dict[str, dict[str, Any]] = {}
        # Compaction baseline (see `compact()`): the state as of
        # `_baseline_version`, replacing every commit at or before it. Absolute
        # `_version`/`_head_id` are preserved; `_commits` holds only the
        # retained tail, so a commit's position is `context_version -
        # _baseline_version - 1`.
        self._baseline: dict[str, Artifact[Any]] | None = None
        self._baseline_relations: dict[RelationKey, Relation] | None = None
        self._baseline_version: int = 0
        self._baseline_head_id: str | None = None

    @property
    def version(self) -> int:
        """Current version (number of applied commits)."""
        return self._version

    @property
    def head_id(self) -> str | None:
        """Id of the last commit (HEAD)."""
        return self._head_id

    def append(self, commit: Commit) -> None:
        """Fills in parent/version and moves head (was `Context.log_commit`)."""
        commit.parent_id = self._head_id
        commit.context_version = self._version + 1
        self._commits.append(commit)
        self._head_id = commit.id
        self._version += 1
        self._index_commit(commit)

    def _index_commit(self, commit: Commit) -> None:
        """Updates `_last_write_commit`/`_dependents` for one appended commit."""
        for write in commit.writes:
            aid = write.artifact_id
            old_sources = self._producing_read_ids.get(aid)
            if old_sources:
                for src in old_sources:
                    deps = self._dependents.get(src)
                    if deps is not None:
                        deps.discard(aid)
                        if not deps:
                            del self._dependents[src]
            self._last_write_commit[aid] = commit
            new_sources = {r.artifact_id for r in commit.reads}
            self._producing_read_ids[aid] = new_sources
            for src in new_sources:
                self._dependents.setdefault(src, set()).add(aid)

    def _rebuild_indices(self) -> None:
        """Full rebuild from `_commits` — used after a bulk rewrite (truncate,
        copy, deserialize) where per-commit incremental updates don't apply."""
        self._last_write_commit = {}
        self._dependents = {}
        self._producing_read_ids = {}
        for commit in self._commits:
            self._index_commit(commit)

    def history(self) -> list[Commit]:
        """Ordered chain of commits from the oldest to head."""
        return list(self._commits)

    def __len__(self) -> int:
        return len(self._commits)

    def commits_from(self, index: int) -> list[Commit]:
        """Commits after context version `index` (used by `checkout` to find
        what a rollback would undo); baseline-aware."""
        return self._commits[max(0, index - self._baseline_version) :]

    def commits_upto(self, upto_version: int) -> list[Commit]:
        """Commits up to and including `upto_version` (used by the replay
        methods); baseline-aware."""
        limit = upto_version - self._baseline_version
        if limit <= 0:
            return []
        return self._commits[:limit]

    @property
    def baseline_version(self) -> int:
        """Highest context version collapsed into the baseline (0 = none)."""
        return self._baseline_version

    def producing_commit(self, artifact_id: str) -> Commit | None:
        """The last commit that wrote the artifact (create or update)."""
        return self._last_write_commit.get(artifact_id)

    def dependents_of(self, artifact_id: str) -> set[str]:
        """Artifact ids whose current producing commit reads `artifact_id`.

        Structural only (ignores versions) — the caller still checks whether
        the dependency is actually stale right now. Scoped to this artifact's
        real dependents, not every artifact in the context.
        """
        return set(self._dependents.get(artifact_id, ()))

    def compact(self, keep_commits: int) -> int:
        """Collapses every commit older than the last `keep_commits` into a
        baseline snapshot.

        Absolute `_version`/`_head_id` are preserved, so `context_hash` and
        version numbers are unchanged; only the operation history of old
        commits is dropped. Returns the number of commits removed.
        Irreversible: replaying/rewinding to a version below the baseline stops
        being exact (see `Context.compact`).
        """
        if keep_commits < 0:
            raise ValueError("keep_commits must be >= 0")
        cutoff = self._version - keep_commits
        if cutoff <= self._baseline_version:
            return 0
        baseline_state = self.replay_state(cutoff)
        baseline_relations = self.replay_relations(cutoff)
        head_at = self._head_at(cutoff)
        self._baseline = {
            aid: Artifact(data=data, id=aid) for aid, data in baseline_state.items()
        }
        self._baseline_relations = baseline_relations
        dropped = cutoff - self._baseline_version
        self._baseline_version = cutoff
        self._baseline_head_id = head_at
        del self._commits[:dropped]
        self._rebuild_indices()
        live_ids = {c.id for c in self._commits}
        self._dict_cache = {
            cid: d for cid, d in self._dict_cache.items() if cid in live_ids
        }
        return dropped

    def _head_at(self, version: int) -> str | None:
        """Head commit id as of `version` (baseline-aware)."""
        if version <= 0:
            return None
        if version <= self._baseline_version:
            return self._baseline_head_id
        return self._commits[version - self._baseline_version - 1].id

    def truncate(self, version: int) -> None:
        """Rolls the log back to `version`: drops later commits, moves head.

        `version=0` means "before any commit" (no head). Refuses to rewind
        below the compaction baseline (that history was discarded).
        """
        if version < self._baseline_version:
            raise ValueError(
                f"cannot rewind to version {version}: history below the "
                f"compaction baseline ({self._baseline_version}) was discarded"
            )
        self._head_id = self._head_at(version)
        del self._commits[max(0, version - self._baseline_version) :]
        self._version = version
        self._rebuild_indices()
        live_ids = {c.id for c in self._commits}
        self._dict_cache = {
            cid: d for cid, d in self._dict_cache.items() if cid in live_ids
        }

    def replay_state(self, upto_version: int) -> dict[str, Any]:
        """Replays the artifact state up to and including `upto_version`.

        Baseline-aware: starts from the compacted snapshot when one exists.
        For `upto_version` below the baseline the baseline state is returned
        (the exact pre-baseline state is no longer reconstructable).
        """
        state: dict[str, Any] = {}
        if self._baseline is not None:
            state = {
                aid: art.data.model_copy(deep=True)
                for aid, art in self._baseline.items()
            }
            if upto_version <= self._baseline_version:
                return state
        for commit in self.commits_upto(upto_version):
            for op in commit.operations:
                if isinstance(op, Create) and op.artifact_id is not None:
                    state[op.artifact_id] = op.data.model_copy(deep=True)
                elif isinstance(op, Update):
                    state[op.artifact_id] = op.new_data.model_copy(deep=True)
                elif isinstance(op, Delete):
                    state.pop(op.artifact_id, None)
        return state

    def replay_relations(self, upto_version: int) -> dict[RelationKey, Relation]:
        """Replays the link graph up to and including `upto_version`;
        baseline-aware (same caveat as `replay_state`)."""
        relations: dict[RelationKey, Relation] = {}
        if self._baseline_relations is not None:
            relations = dict(self._baseline_relations)
            if upto_version <= self._baseline_version:
                return relations
        for commit in self.commits_upto(upto_version):
            for op in commit.operations:
                if isinstance(op, Link):
                    key = (op.artifact_id, op.relation, op.target_id)
                    relations[key] = Relation(*key)
                elif isinstance(op, Unlink):
                    for key in list(relations.keys()):
                        if key[0] != op.artifact_id:
                            continue
                        if op.relation is not None and key[1] != op.relation:
                            continue
                        if op.target_id is not None and key[2] != op.target_id:
                            continue
                        del relations[key]
        return relations

    def copy(self) -> CommitLog:
        clone = CommitLog()
        clone._commits = copy.deepcopy(self._commits)
        clone._version = self._version
        clone._head_id = self._head_id
        clone._baseline = (
            {aid: copy.deepcopy(art) for aid, art in self._baseline.items()}
            if self._baseline is not None
            else None
        )
        clone._baseline_relations = (
            dict(self._baseline_relations)
            if self._baseline_relations is not None
            else None
        )
        clone._baseline_version = self._baseline_version
        clone._baseline_head_id = self._baseline_head_id
        # Deep-copied commits are new objects — re-derive the indices instead
        # of copying dicts that would still point at the originals.
        clone._rebuild_indices()
        # A commit's serialized form only depends on its (unchanged-by-copy)
        # field values, keyed by its (unchanged-by-copy) id — safe to carry
        # the cache over instead of re-serializing on the clone's first save.
        clone._dict_cache = dict(self._dict_cache)
        return clone

    def to_baseline_dict(self) -> dict[str, Any] | None:
        """Serializes the compaction baseline (`None` when never compacted)."""
        if self._baseline is None:
            return None
        return {
            "version": self._baseline_version,
            "head_id": self._baseline_head_id,
            "artifacts": {aid: art.to_dict() for aid, art in self._baseline.items()},
            "relations": [
                rel.to_dict() for rel in (self._baseline_relations or {}).values()
            ],
        }

    def to_dict(self) -> list[dict[str, Any]]:
        """Serializes the commit chain, memoized per commit id.

        A commit is immutable once appended (`writes` is filled in before
        `Context.log_commit()` and never touched again), so re-serializing an
        already-serialized commit on every `Context.to_dict()`/session save
        is pure waste for a long-lived context — only new commits since the
        last call actually get `Commit.to_dict()` called on them.
        """
        result = []
        for c in self._commits:
            cached = self._dict_cache.get(c.id)
            if cached is None:
                cached = c.to_dict()
                self._dict_cache[c.id] = cached
            result.append(cached)
        return result

    @classmethod
    def from_dict(
        cls,
        commits: list[dict[str, Any]],
        *,
        version: int | None,
        head_id: str | None,
        baseline: dict[str, Any] | None = None,
    ) -> CommitLog:
        log = cls()
        log._commits = [Commit.from_dict(cd) for cd in commits]
        if baseline is not None:
            log._baseline = {
                aid: Artifact.from_dict(art_dict)
                for aid, art_dict in baseline["artifacts"].items()
            }
            log._baseline_relations = {
                (rel["source_id"], rel["relation"], rel["target_id"]): Relation(
                    rel["source_id"], rel["relation"], rel["target_id"]
                )
                for rel in baseline.get("relations", [])
            }
            log._baseline_version = int(baseline["version"])
            log._baseline_head_id = baseline.get("head_id")
        log._version = (
            version
            if version is not None
            else log._baseline_version + len(log._commits)
        )
        log._head_id = (
            head_id
            if head_id is not None
            else (log._commits[-1].id if log._commits else log._baseline_head_id)
        )
        log._rebuild_indices()
        return log


__all__ = ["CommitLog"]
