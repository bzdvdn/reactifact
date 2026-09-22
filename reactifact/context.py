from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

from .artifacts import Artifact
from .checkpoints import CheckpointBackend, FileBackend, KVBackend
from .commit import Commit
from .commit_log import CommitLog
from .events import Event, EventType
from .interrupt import PendingQuestion
from .patches import (
    Create,
    Delete,
    Relation,
    Update,
)
from .relations import RelationGraph
from .resources import RuntimeResources
from .streaming import EventHub, ProgressEvent, QueueEvent

TData = TypeVar("TData", bound=BaseModel)
TArtifact = TypeVar("TArtifact", bound=BaseModel)


@dataclass
class View:
    """Context projection for an agent: artifact references + serialization (§27).

    Used for building the prompt and controlling the token budget (§58):
    `tokens_estimate` is a rough estimate (≈4 characters per token).
    """

    artifacts: list[Artifact[Any]] = field(default_factory=list)

    def render(self, *, max_chars: int | None = None) -> str:
        """Serializes artifacts into compact text for the prompt.

        Each artifact is a line `[Type] {json}`. `max_chars` truncates the result.
        """
        lines = [
            f"[{type(a.data).__name__}] "
            + json.dumps(a.data.model_dump(mode="json"), ensure_ascii=False)
            for a in self.artifacts
        ]
        text = "\n".join(lines)
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars]
        return text

    @property
    def tokens_estimate(self) -> int:
        """Rough estimate of the prompt size in tokens (≈4 chars/token, §58)."""
        return max(1, len(self.render()) // 4)


class Context:
    """Central artifact store with an event queue.

    Git-like model: every applied commit forms a new Context version (head).
    Commits chain through parent_id and carry a reads/writes trace — the actual
    agent linkage via consumes/produces.
    """

    def __init__(self, resources: RuntimeResources | None = None):
        self._artifacts: dict[str, Artifact[Any]] = {}
        self._events: list[Event] = []
        self._log = CommitLog()
        self.resources = resources or RuntimeResources()
        self._hub = EventHub()
        self._relations = RelationGraph()
        self._base: Context | None = None
        self._fork_name: str = ""
        # Incrementally maintained (§ dependents index): artifact ids whose
        # producing commit read a source that has since moved to a newer
        # version. Kept up to date by `update()`/`log_commit()` so
        # `stale_artifacts()`/`has_stale()` never rescan the whole context.
        self._stale: set[str] = set()
        # Incrementally maintained: exact `type(data)` -> ids of that exact
        # type. Kept up to date by `create()`/`update()`/`delete()` so
        # `list_artifacts(T)` doesn't call `isinstance()` per artifact — it
        # unions the ids of every *distinct type ever created* that is an
        # `issubclass` of `T` (a small set — bounded by distinct types, not
        # artifact count) into an O(1) membership test, still walked in
        # insertion order to preserve tie-break order in callers' own sorts.
        # Any bulk rewrite that bypasses create/update/delete must call
        # `_reindex_by_type()` (mirrors `_recompute_stale()`, same reasoning).
        self._by_type: dict[type, set[str]] = {}

    # ---- announce: agent progress events streamed out ----

    def announce(self, message: str, *, kind: str = "status", **data: Any) -> None:
        """Publishes a progress event to active streams (no-op without subscribers).

        `kind` is a category for the application: "status" (domain agent statuses),
        "agent" (internal, from framework producers like ToolUse).
        The application itself decides which kinds to show the user.
        """
        if self._hub.has_subscribers:
            self._hub.publish(
                ProgressEvent(
                    kind=kind,
                    message=message,
                    data=data,
                )
            )

    def subscribe(self) -> QueueEvent:
        return self._hub.subscribe()

    def unsubscribe(self, queue: QueueEvent) -> None:
        self._hub.unsubscribe(queue)

    def create(self, data: TData, id: str | None = None) -> Artifact[TData]:
        """Creates a new artifact and generates an ARTIFACT_CREATED event.

        If a stable id is given and an artifact with it already exists, returns
        the existing one without creating a duplicate or an event (idempotency, §42).
        """
        if id is not None and id in self._artifacts:
            return self._artifacts[id]
        if id is None and self.resources.id_factory is not None:
            id = self.resources.id_factory(type(data).__name__)
        artifact = Artifact(data=data, id=id)
        self._artifacts[artifact.id] = artifact
        self._by_type.setdefault(type(data), set()).add(artifact.id)
        self._events.append(
            Event(
                type=EventType.ARTIFACT_CREATED,
                artifact_type=type(data),
                artifact_id=artifact.id,
            )
        )
        return artifact

    def get(self, artifact_id: str) -> Artifact[Any] | None:
        """Returns the artifact by id or None."""
        return self._artifacts.get(artifact_id)

    def update(self, artifact_id: str, new_data: TData) -> Artifact[TData] | None:
        """Updates artifact data, creates a new version, generates ARTIFACT_UPDATED.

        If the data did not change, the version and the event are left untouched:
        no-op patches must not cascade into reactions (§41, §42).
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return None
        if artifact.data == new_data:
            return artifact
        old_type = type(artifact.data)
        artifact.update(new_data)
        new_type = type(new_data)
        if new_type is not old_type:
            self._by_type.get(old_type, set()).discard(artifact_id)
            self._by_type.setdefault(new_type, set()).add(artifact_id)
        self._events.append(
            Event(
                type=EventType.ARTIFACT_UPDATED,
                artifact_type=type(new_data),
                artifact_id=artifact.id,
            )
        )
        for dependent in self._dependents_of(artifact_id):
            self._events.append(
                Event(
                    type=EventType.ARTIFACT_STALE,
                    artifact_type=type(dependent.data),
                    artifact_id=dependent.id,
                )
            )
            self._stale.add(dependent.id)
        return artifact

    def delete(self, artifact_id: str) -> bool:
        """Deletes the artifact and generates ARTIFACT_DELETED."""
        artifact = self._artifacts.pop(artifact_id, None)
        if artifact is None:
            return False
        self._stale.discard(artifact_id)
        self._by_type.get(type(artifact.data), set()).discard(artifact_id)
        self._events.append(
            Event(
                type=EventType.ARTIFACT_DELETED,
                artifact_type=type(artifact.data),
                artifact_id=artifact.id,
            )
        )
        return True

    @overload
    def list_artifacts(self, artifact_type: None = None) -> list[Artifact[Any]]: ...

    @overload
    def list_artifacts(
        self, artifact_type: type[TArtifact]
    ) -> list[Artifact[TArtifact]]: ...

    def list_artifacts(
        self, artifact_type: type[TArtifact] | None = None
    ) -> list[Artifact[Any]]:
        """Returns a list of artifacts, optionally filtered by data type.

        Preserves insertion order (matters: ties in a caller's own sort key,
        e.g. `updated_at`, break in creation order, same as before this
        method stopped `isinstance`-scanning every artifact).
        """
        if artifact_type is None:
            return list(self._artifacts.values())
        matching_ids: set[str] = set()
        for t, ids in self._by_type.items():
            if issubclass(t, artifact_type):
                matching_ids |= ids
        return [
            cast(Artifact[TArtifact], a)
            for aid, a in self._artifacts.items()
            if aid in matching_ids
        ]

    def latest(self, artifact_type: type[TArtifact]) -> Artifact[TArtifact] | None:
        """The most recently created artifact of a type, or None.

        Sugar over `list_artifacts` for "grab the latest answer/finding" —
        the common read after a run.
        """
        artifacts = self.list_artifacts(artifact_type)
        if not artifacts:
            return None
        return max(artifacts, key=lambda a: a.created_at)

    # ---- Context Views (§27): projection for the agent/prompt within the budget ----

    def view(
        self,
        artifact_type: type[BaseModel] | tuple[type[BaseModel], ...] | None = None,
        *,
        condition: Callable[[Artifact[Any]], bool] | None = None,
        limit: int | None = None,
    ) -> View:
        """Artifact projection for the agent (§27): by type/condition/limit.

        The View does not copy state — it is references to artifacts plus
        serialization for the prompt. `tokens_estimate` lets the agent stay within
        the token budget (§58): build the view, check the estimate, reduce `limit`
        if needed.
        """
        if artifact_type is None:
            artifacts = list(self._artifacts.values())
        else:
            artifacts = [
                a for a in self._artifacts.values() if isinstance(a.data, artifact_type)
            ]
        if condition is not None:
            artifacts = [a for a in artifacts if condition(a)]
        if limit is not None:
            artifacts = artifacts[:limit]
        return View(artifacts=artifacts)

    # ---- Relations: the artifact graph (§15) ----

    def link(self, source_id: str, relation: str, target_id: str) -> Relation:
        """Establishes a link `source_id —relation→ target_id` (idempotently, §42)."""
        return self._relations.link(source_id, relation, target_id)

    def unlink(
        self,
        source_id: str,
        relation: str | None = None,
        target_id: str | None = None,
    ) -> int:
        """Removes links; `relation`/`target_id` = None mean "any"."""
        return self._relations.unlink(source_id, relation, target_id)

    def relations(
        self,
        source_id: str | None = None,
        relation: str | None = None,
        target_id: str | None = None,
    ) -> list[Relation]:
        """All links, optionally filtered by any edge component."""
        return self._relations.relations(source_id, relation, target_id)

    def incoming(self, target_id: str, relation: str | None = None) -> list[Relation]:
        """Links pointing at `target_id` (for provenance: who references what)."""
        return self.relations(target_id=target_id, relation=relation)

    def related(
        self, source_id: str, relation: str | None = None
    ) -> list[Artifact[Any]]:
        """Target artifacts of outgoing links (existing ones; "dangling" ones are skipped)."""
        targets: list[Artifact[Any]] = []
        seen: set[str] = set()
        for rel in self.relations(source_id=source_id, relation=relation):
            artifact = self._artifacts.get(rel.target_id)
            if artifact is not None and rel.target_id not in seen:
                targets.append(artifact)
                seen.add(rel.target_id)
        return targets

    def dangling_relations(self) -> list[Relation]:
        """Links with a non-existent source or target (§69): the state is visible,
        not hidden in a string."""
        return [
            rel
            for rel in self._relations.values()
            if rel.source_id not in self._artifacts
            or rel.target_id not in self._artifacts
        ]

    # ---- HITL: questions awaiting a human answer ----

    def interrupt(
        self,
        question: str,
        *,
        kind: str = "general",
        notes: dict[str, Any] | None = None,
    ) -> Artifact[PendingQuestion]:
        """Poses a question to a human: creates a PendingQuestion in the context."""
        return self.create(
            PendingQuestion(question=question, kind=kind, notes=notes or {})
        )

    def pending_questions(self) -> list[Artifact[PendingQuestion]]:
        """Unanswered questions awaiting the human."""
        return [a for a in self.list_artifacts(PendingQuestion) if not a.data.answered]

    def has_pending_question(self) -> bool:
        return bool(self.pending_questions())

    def latest_pending_question(self) -> Artifact[PendingQuestion] | None:
        questions = self.pending_questions()
        if not questions:
            return None
        return max(questions, key=lambda a: a.created_at)

    def resume(self, question_id: str, answer: str) -> Artifact[PendingQuestion] | None:
        """A human's answer is a regular patch: marks the question as answered.

        Generates ARTIFACT_UPDATED, which agents subscribed to
        PendingQuestion(answered=True) react to.
        """
        artifact = self._artifacts.get(question_id)
        if artifact is None or not isinstance(artifact.data, PendingQuestion):
            return None
        updated = artifact.data.model_copy(
            update={
                "answered": True,
                "resolution": answer,
                "resolved_at": datetime.now(UTC),
            }
        )
        return self.update(question_id, updated)

    def drain_events(self) -> list[Event]:
        """Drains and clears the event queue."""
        events = self._events
        self._events = []
        return events

    def clone(self) -> Context:
        """Deep copy of this context's live state. See `reactifact.branching`."""
        from .branching import clone_context

        return clone_context(self)

    def merge_from(self, other: Context) -> None:
        """Two-way merge, no conflict detection. See `reactifact.branching`."""
        from .branching import merge_context_from

        merge_context_from(self, other)

    def branch(self, *, name: str = "") -> Context:
        """Forks an isolated copy for alternative state exploration (§39).

        The fork records a snapshot of its base, so a later `merge` of two
        fork-mates can detect diverged artifacts three-way (§40). The branch
        shares `resources` with the parent but is otherwise fully independent:
        subsequent changes on either side do not affect the other. Algorithm
        lives in `reactifact.branching.fork_context`.
        """
        from .branching import fork_context

        return fork_context(self, name=name)

    def merge(self, other: Context, *, message: str = "Merged branch") -> None:
        """Merges `other` into `self` with explicit conflicts, atomically (§40).

        Three-way merge against the shared fork base (the fork snapshot of
        `other`, or of `self` when `other` has none) — raises `MergeConflict`
        (`reactifact.branching.MergeConflict`, re-exported as `reactifact.MergeConflict`)
        rather than silently choosing a side. Algorithm lives in
        `reactifact.branching.merge_contexts`.
        """
        from .branching import merge_contexts

        merge_contexts(self, other, message=message)

    def log_commit(self, commit: Commit) -> None:
        """Applies the commit to the repository: fills in parent/version, moves head."""
        self._log.append(commit)
        # A commit that (re-)writes an artifact refreshes it against its
        # current reads — it can no longer be in the stale set.
        for write in commit.writes:
            self._stale.discard(write.artifact_id)

    def commit_log(self) -> list[Commit]:
        return self._log.history()

    @property
    def version(self) -> int:
        """Current Context version (number of applied commits)."""
        return self._log.version

    @property
    def head_id(self) -> str | None:
        """Id of the last commit (HEAD)."""
        return self._log.head_id

    def history(self) -> list[Commit]:
        """History: an ordered chain of commits from the oldest to head."""
        return self._log.history()

    def diff(self, version_a: int, version_b: int) -> dict[str, Any]:
        """State delta between two Context versions.

        Returns {"added": {id: data}, "removed": {id: data}, "changed": {id: {old, new}}}.
        The diff compares the versioned state (commits); artifacts created directly
        outside commits (the "working tree") do not participate.
        """
        if not (0 <= version_a <= version_b <= self._log.version):
            raise ValueError(
                f"Invalid versions: {version_a}..{version_b} (head={self._log.version})"
            )
        snap_a = self._log.replay_state(version_a)
        snap_b = self._log.replay_state(version_b)
        result: dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
        for aid in snap_b.keys() - snap_a.keys():
            result["added"][aid] = snap_b[aid].model_dump()
        for aid in snap_a.keys() - snap_b.keys():
            result["removed"][aid] = snap_a[aid].model_dump()
        for aid in snap_a.keys() & snap_b.keys():
            if snap_a[aid] != snap_b[aid]:
                result["changed"][aid] = {
                    "old": snap_a[aid].model_dump(),
                    "new": snap_b[aid].model_dump(),
                }
        return result

    def snapshot(self) -> dict[str, Any]:
        """Consistent snapshot of the current artifact state (id → data)."""
        return {aid: art.data.model_dump() for aid, art in self._artifacts.items()}

    # ---- Invalidation / staleness (§43–44) based on recorded reads ----

    def _producing_commit(self, artifact_id: str) -> Commit | None:
        """The last commit that wrote the artifact (create or update)."""
        return self._log.producing_commit(artifact_id)

    def _dependents_of(self, artifact_id: str) -> list[Artifact[Any]]:
        """Artifacts whose producing commit read `artifact_id` at an older version.

        Looks up only `artifact_id`'s actual dependents via the commit log's
        reverse index (`CommitLog.dependents_of`), not every artifact in the
        context — used right after that artifact's version bumps to emit
        `ARTIFACT_STALE` reactively instead of waiting for a `stale_artifacts()`
        poll.
        """
        current = self._artifacts.get(artifact_id)
        if current is None:
            return []
        dependents: list[Artifact[Any]] = []
        for aid in self._log.dependents_of(artifact_id):
            artifact = self._artifacts.get(aid)
            if artifact is None:
                continue
            commit = self._producing_commit(aid)
            if commit is None:
                continue
            for read in commit.reads:
                if read.artifact_id == artifact_id and read.version < current.version:
                    dependents.append(artifact)
                    break
        return dependents

    def stale_artifacts(self) -> list[Artifact[Any]]:
        """Artifacts whose parents (reads in the producing commit) are now newer versions.

        Dependencies are built from the actual reads recorded by the runtime via
        consumes — a link derived from execution, not an author-drawn graph.
        Backed by the incrementally-maintained `_stale` set (kept current by
        `update()`/`log_commit()`), not a rescan of every artifact.
        """
        return [self._artifacts[aid] for aid in self._stale if aid in self._artifacts]

    def has_stale(self) -> bool:
        return bool(self._stale)

    def _recompute_stale(self) -> None:
        """Full rebuild of `_stale` from current artifacts + the commit log.

        Only needed after a bulk rewrite that bypasses the normal
        `create`/`update`/`log_commit` path (`checkout`, `clone`, `from_dict`)
        — those are inherently O(state) operations already, unlike the hot
        `update()`/`stale_artifacts()` path this index exists to keep cheap.
        """
        stale: set[str] = set()
        for artifact_id in self._artifacts:
            commit = self._producing_commit(artifact_id)
            if commit is None:
                continue
            for read in commit.reads:
                current = self._artifacts.get(read.artifact_id)
                if current is not None and current.version > read.version:
                    stale.add(artifact_id)
                    break
        self._stale = stale

    def _reindex_by_type(self) -> None:
        """Full rebuild of `_by_type` from current artifacts.

        Same reasoning as `_recompute_stale()`: only needed after a bulk
        rewrite that bypasses `create`/`update`/`delete` — call it alongside
        `_recompute_stale()` at every such site, never on its own.
        """
        by_type: dict[type, set[str]] = {}
        for aid, artifact in self._artifacts.items():
            by_type.setdefault(type(artifact.data), set()).add(aid)
        self._by_type = by_type

    def _rebuild_artifacts_from_commits(
        self, upto_version: int
    ) -> dict[str, Artifact[Any]]:
        state = self._log.replay_state(upto_version)
        return {aid: Artifact(data=data, id=aid) for aid, data in state.items()}

    def checkout(self, version: int) -> None:
        """Moves head back to a previous version (rollback along the commit chain).

        Artifacts not part of the versioned history (created directly outside
        commits — the "working tree") are preserved.
        """
        if not (0 <= version <= self._log.version):
            raise ValueError(
                f"Invalid checkout version: {version} (head={self._log.version})"
            )
        touched: set[str] = set()
        for commit in self._log.commits_from(version):
            for op in commit.operations:
                if isinstance(op, (Create, Update, Delete)) and op.artifact_id:
                    touched.add(op.artifact_id)
        rebuilt = self._rebuild_artifacts_from_commits(version)
        for aid, art in self._artifacts.items():
            if aid not in touched:
                rebuilt[aid] = art
        self._artifacts = rebuilt

        # Relations: those committed up to `version` plus the "working tree"
        # (created directly outside commits) are kept, as with artifacts. Links
        # introduced by commits in the [version:] range are rolled back.
        committed_now = self._log.replay_relations(self._log.version)
        working_tree_rels = {
            key: rel for key, rel in self._relations.items() if key not in committed_now
        }
        self._relations = RelationGraph.from_mapping(
            {**self._log.replay_relations(version), **working_tree_rels}
        )

        self._events = []
        self._log.truncate(version)
        self._recompute_stale()
        self._reindex_by_type()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self._log.version,
            "head_id": self._log.head_id,
            "artifacts": {aid: art.to_dict() for aid, art in self._artifacts.items()},
            "relations": self._relations.to_dict(),
            "commits": self._log.to_dict(),
            "fork_name": self._fork_name,
            "base": self._base.to_dict() if self._base is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Context:
        ws = cls()
        for aid, art_dict in d["artifacts"].items():
            artifact = Artifact.from_dict(art_dict)
            ws._artifacts[aid] = artifact
        ws._relations = RelationGraph.from_dict(d.get("relations", []))
        ws._log = CommitLog.from_dict(
            d["commits"], version=d.get("version"), head_id=d.get("head_id")
        )
        ws._fork_name = d.get("fork_name", "")
        ws._base = Context.from_dict(d["base"]) if d.get("base") is not None else None
        ws._recompute_stale()
        ws._reindex_by_type()
        return ws

    async def save_checkpoint(self, backend_or_path: str | CheckpointBackend) -> None:
        backend: CheckpointBackend
        if isinstance(backend_or_path, str):
            backend = FileBackend(backend_or_path)
        else:
            backend = backend_or_path
        await backend.save(self.to_dict())

    @classmethod
    async def load_checkpoint(cls, backend_or_path: str | CheckpointBackend) -> Context:
        backend: CheckpointBackend
        if isinstance(backend_or_path, str):
            backend = FileBackend(backend_or_path)
        else:
            backend = backend_or_path
        data = await backend.load()
        return cls.from_dict(data)

    async def to_kv(self, backend: KVBackend, key: str) -> None:
        """Serializes and stores this context under `key` in a KV backend.

        The one `to_dict()` round-trip shared by `SessionStore`/`BranchStore`
        (session_id / branch keys are just a naming convention over the same
        backend, §39) — call this instead of hand-rolling `backend.set(key,
        context.to_dict())`.
        """
        await backend.set(key, self.to_dict())

    @classmethod
    async def from_kv(cls, backend: KVBackend, key: str) -> Context | None:
        """Loads a context previously stored with `to_kv`, or None if absent."""
        data = await backend.get(key)
        return cls.from_dict(data) if data is not None else None

    def __repr__(self) -> str:
        return f"<Context artifacts={len(self._artifacts)} pending_events={len(self._events)}>"
