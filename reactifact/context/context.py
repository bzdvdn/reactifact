from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

from ..artifacts import (
    Artifact,
    ArtifactStore,
    ArtifactVersion,
)
from ..checkpoints import CheckpointBackend, FileBackend, KVBackend
from ..commit import Commit
from ..commit_log import CommitLog
from ..events import Event, EventType
from ..interrupt import PendingAction, PendingQuestion
from ..patches import (
    Create,
    Delete,
    Link,
    Relation,
    Unlink,
    Update,
)
from ..relations import RelationGraph
from ..resources import RuntimeResources
from ..streaming import EventHub, ProgressEvent, QueueEvent
from .index import dependents_of, recompute_stale, reindex_by_type
from .serialization import (
    CONTEXT_FORMAT_VERSION as CONTEXT_FORMAT_VERSION,
)
from .serialization import (
    SerializedContext as SerializedContext,
)
from .serialization import (
    migrate_context_payload as migrate_context_payload,
)

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

    @property
    def request(self) -> Mapping[str, Any]:
        """The active turn's request mapping (empty when none was set).

        Read-only view of `reactifact.request.current_request()` — the same
        data a produce sees as `call.request`. Exposed on `Context` so hooks
        that receive only a context (e.g. `ChatAssistant(create_message=…)`)
        can read per-turn metadata (category, tenant, user) without a
        hand-rolled `ContextVar` of their own.
        """
        from ..request import current_request

        return current_request()

    def __init__(self, resources: RuntimeResources | None = None):
        self._artifacts: dict[str, Artifact[Any]] = {}
        self._store = ArtifactStore()
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
        # Per storage key: (version, compaction baseline) already persisted.
        # A Context can be saved under several keys (a session + named
        # branches), so this is keyed rather than a single global marker.
        self._persisted: dict[str, tuple[int, int]] = {}
        #: Per-step input cursors (watermark scheduling): step key →
        #: {artifact type: (latest id, max version)}. In-memory for now;
        #: persistence/replay of cursors is a later core milestone.
        self._step_cursors: dict[Any, dict[type, dict[str, int]]] = {}
        # Compatibility view for the single-session case (last marked key).
        self._persisted_version = -1
        self._persisted_compacted_at = 0

    def persisted_state(self, key: str) -> tuple[int, int]:
        """`(version, compacted_at)` already written for `key`.

        `(-1, 0)` when this Context has never been persisted under that key.
        """
        return self._persisted.get(key, (-1, 0))

    @property
    def persisted_version(self) -> int:
        """Highest version written by the last `mark_persisted` call
        (`-1` when this Context has never been persisted)."""
        return self._persisted_version

    @property
    def persisted_compacted_at(self) -> int:
        """Compaction baseline written by the last `mark_persisted` call."""
        return self._persisted_compacted_at

    def mark_persisted(self, version: int | None = None, *, key: str = "") -> None:
        """Records that the Context has been persisted up to `version` (head
        when omitted) for `key`, including its current compaction baseline."""
        persisted = self.version if version is None else version
        self._persisted[key] = (persisted, self.compacted_at)
        self._persisted_version = persisted
        self._persisted_compacted_at = self.compacted_at

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
        self._store.create(data, artifact.id, created_at=artifact.created_at)
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
        self._store.update(artifact_id, new_data)
        updated = self._store.current_artifact(artifact_id)
        assert updated is not None
        self._artifacts[artifact_id] = updated
        new_type = type(new_data)
        if new_type is not old_type:
            self._by_type.get(old_type, set()).discard(artifact_id)
            self._by_type.setdefault(new_type, set()).add(artifact_id)
        self._events.append(
            Event(
                type=EventType.ARTIFACT_UPDATED,
                artifact_type=type(new_data),
                artifact_id=artifact_id,
            )
        )
        for dependent in dependents_of(self, artifact_id):
            self._events.append(
                Event(
                    type=EventType.ARTIFACT_STALE,
                    artifact_type=type(dependent.data),
                    artifact_id=dependent.id,
                )
            )
            self._stale.add(dependent.id)
        return updated

    def delete(self, artifact_id: str) -> bool:
        """Deletes the artifact and generates ARTIFACT_DELETED."""
        artifact = self._artifacts.pop(artifact_id, None)
        if artifact is None:
            return False
        self._stale.discard(artifact_id)
        self._store.delete(artifact_id)
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
        kind: str = "general",
        notes: dict[str, Any] | None = None,
    ) -> Artifact[PendingQuestion]:
        from .pending import interrupt

        return interrupt(self, question, kind=kind, notes=notes)

    def pending_questions(self) -> list[Artifact[PendingQuestion]]:
        from .pending import pending_questions

        return pending_questions(self)

    def has_pending_question(self) -> bool:
        from .pending import has_pending_question

        return has_pending_question(self)

    def latest_pending_question(self) -> Artifact[PendingQuestion] | None:
        from .pending import latest_pending_question

        return latest_pending_question(self)

    def resume(self, question_id: str, answer: str) -> Artifact[PendingQuestion] | None:
        from .pending import resume

        return resume(self, question_id, answer)

    def pending_actions(self) -> list[Artifact[PendingAction]]:
        from .pending import pending_actions

        return pending_actions(self)

    def mark_dispatched(self, action_id: str) -> Artifact[PendingAction] | None:
        from .pending import mark_dispatched

        return mark_dispatched(self, action_id)

    def mark_failed(
        self, action_id: str, error: str | None = None
    ) -> Artifact[PendingAction] | None:
        from .pending import mark_failed

        return mark_failed(self, action_id, error)

    def retry_action(self, action_id: str) -> Artifact[PendingAction] | None:
        from .pending import retry_action

        return retry_action(self, action_id)

    def drain_events(self) -> list[Event]:
        """Drains and clears the event queue."""
        events = self._events
        self._events = []
        return events

    def pending_events(self) -> list[Event]:
        """A snapshot of the queued events, without consuming them.

        Non-destructive counterpart of `drain_events()` (which stays for
        callers that explicitly want to flush the queue — tests, seeding).
        The runtime reads a batch with this, schedules the generation, and
        only calls `consume_events()` once the generation's patches are
        committed: a generation that raises leaves its triggers queued
        instead of silently dropping the consumers they would have woken, so
        a retry re-runs the work rather than settling at zero runs on an
        empty queue.
        """
        return list(self._events)

    def consume_events(self, events: list[Event]) -> None:
        """Removes a batch previously read with `pending_events()`.

        Matching is by identity, not by count: events appended while the
        generation ran — its commits' effects, staged for the *next*
        generation — are preserved, and it stays correct even if a produce
        drained or reordered the queue directly during the generation.
        """
        if not events:
            return
        consumed = {id(event) for event in events}
        self._events = [event for event in self._events if id(event) not in consumed]

    def clone(self) -> Context:
        """Deep copy of this context's live state. See `reactifact.branching`."""
        from ..branching.ops import clone_context

        return clone_context(self)

    def merge_from(self, other: Context) -> None:
        """Two-way merge, no conflict detection. See `reactifact.branching`."""
        from ..branching.ops import merge_context_from

        merge_context_from(self, other)

    def branch(self, *, name: str = "") -> Context:
        """Forks an isolated copy for alternative state exploration (§39).

        The fork records a snapshot of its base, so a later `merge` of two
        fork-mates can detect diverged artifacts three-way (§40). The branch
        shares `resources` with the parent but is otherwise fully independent:
        subsequent changes on either side do not affect the other. Algorithm
        lives in `reactifact.branching.fork_context`.
        """
        from ..branching.ops import fork_context

        return fork_context(self, name=name)

    def merge(self, other: Context, *, message: str = "Merged branch") -> None:
        """Merges `other` into `self` with explicit conflicts, atomically (§40).

        Three-way merge against the shared fork base (the fork snapshot of
        `other`, or of `self` when `other` has none) — raises `MergeConflict`
        (`reactifact.branching.MergeConflict`, re-exported as `reactifact.MergeConflict`)
        rather than silently choosing a side. Algorithm lives in
        `reactifact.branching.merge_contexts`.
        """
        from ..branching.ops import merge_contexts

        merge_contexts(self, other, message=message)

    def log_commit(self, commit: Commit) -> None:
        """Applies the commit to the repository: fills in parent/version, moves head."""
        self._log.append(commit)
        self._store.bind_commit(commit)
        for write in commit.writes:
            if write.op_type == "delete":
                continue
            refreshed = self._store.current_artifact(write.artifact_id)
            if refreshed is not None:
                self._artifacts[write.artifact_id] = refreshed
        # A commit that (re-)writes an artifact refreshes it against its
        # current reads — it can no longer be in the stale set.
        for write in commit.writes:
            self._stale.discard(write.artifact_id)

    def apply_commit(self, commit: Commit, *, reindex: bool = True) -> None:
        """Applies a commit's operations to the live state and logs it.

        This is the load path for the append-only session format (snapshot +
        commit log): it replays operations *without* emitting events (a
        restored session must not re-trigger its own agents). `reindex=False`
        skips the final full re-index — use it when applying several commits
        and call `rebuild_indices()` once at the end.
        """
        for op in commit.operations:
            if isinstance(op, Create):
                artifact_id = op.artifact_id or op.id
                if artifact_id is None:
                    raise ValueError(
                        "cannot apply a Create without artifact_id/id to a Context"
                    )
                existing = self._artifacts.get(artifact_id)
                if existing is not None:
                    self._by_type.get(type(existing.data), set()).discard(artifact_id)
                    self._store.update(
                        artifact_id,
                        op.data,
                        commit_id=commit.id,
                        created_at=commit.timestamp,
                    )
                    self._by_type.setdefault(type(op.data), set()).add(artifact_id)
                else:
                    self._store.create(
                        op.data,
                        artifact_id,
                        commit_id=commit.id,
                        created_at=commit.timestamp,
                    )
                    self._by_type.setdefault(type(op.data), set()).add(artifact_id)
                applied = self._store.current_artifact(artifact_id)
                assert applied is not None
                self._artifacts[artifact_id] = applied
            elif isinstance(op, Update):
                artifact = self._artifacts.get(op.artifact_id)
                if artifact is None:
                    continue
                old_type = type(artifact.data)
                self._store.update(
                    op.artifact_id,
                    op.new_data,
                    commit_id=commit.id,
                    created_at=commit.timestamp,
                )
                applied = self._store.current_artifact(op.artifact_id)
                assert applied is not None
                self._artifacts[op.artifact_id] = applied
                new_type = type(op.new_data)
                if new_type is not old_type:
                    self._by_type.get(old_type, set()).discard(op.artifact_id)
                    self._by_type.setdefault(new_type, set()).add(op.artifact_id)
            elif isinstance(op, Delete):
                artifact = self._artifacts.pop(op.artifact_id, None)
                if artifact is not None:
                    self._store.delete(op.artifact_id)
                    self._by_type.get(type(artifact.data), set()).discard(
                        op.artifact_id
                    )
            elif isinstance(op, Link):
                self.link(op.artifact_id, op.relation, op.target_id)
            elif isinstance(op, Unlink):
                self.unlink(op.artifact_id, op.relation, op.target_id)
            else:
                raise ValueError(f"Unknown operation: {op}")
        self.log_commit(commit)
        if reindex:
            self.rebuild_indices()

    def rebuild_indices(self) -> None:
        """Rebuilds the type/staleness indices from the live state.

        Used after a bulk load (append-only replay) that bypasses the
        incremental `create`/`update`/`delete` paths.
        """
        recompute_stale(self)
        reindex_by_type(self)

    def replace_events(self, events: Sequence[Event]) -> None:
        """Replaces the pending-event queue (used by the session load path)."""
        self._events = list(events)

    def commit_log(self) -> list[Commit]:
        return self._log.history()

    def commit_graph(self) -> dict[str, list[str]]:
        """The commit DAG as `{commit id: [parent ids]}`."""
        return self._log.graph()

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

    def compact(self, *, keep_commits: int, keep_versions: int | None = None) -> int:
        """Bounds memory: squash old commit history into a baseline snapshot.

        Keeps the last `keep_commits` commits replayable and collapses
        everything older into a baseline; the absolute `version`/`head_id` and
        therefore `context_hash` are unchanged (this is the history of *how*
        state got here, not the state). With `keep_versions`, each artifact's
        retained version history is also trimmed to its last `keep_versions`
        (0 drops it) — `Artifact.version` itself does not change.

        Irreversible, like squashing git history: `checkout`/`diff` below the
        resulting baseline raise, and provenance for artifacts last written
        before it is no longer available. Call it between turns. Returns the
        number of commits collapsed.
        """
        dropped = self._log.compact(keep_commits)
        if keep_versions is not None:
            for artifact_id in self._artifacts:
                self._store.trim_history(artifact_id, keep_versions)
        if dropped:
            recompute_stale(self)
        return dropped

    @property
    def compacted_at(self) -> int:
        """Highest version collapsed into the baseline (0 = never compacted)."""
        return self._log.baseline_version

    def diff(self, version_a: int, version_b: int) -> dict[str, Any]:
        """State delta between two Context versions.

        Returns {"added": {id: data}, "removed": {id: data}, "changed": {id: {old, new}}}.
        The diff compares the versioned state (commits); artifacts created directly
        outside commits (the "working tree") do not participate.
        """
        if version_a < self._log.baseline_version:
            raise ValueError(
                f"cannot diff from version {version_a}: history below the "
                f"compaction baseline ({self._log.baseline_version}) was discarded"
            )
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

    def stale_artifacts(self) -> list[Artifact[Any]]:
        from .index import stale_artifacts

        return stale_artifacts(self)

    def has_stale(self) -> bool:
        from .index import has_stale

        return has_stale(self)

    def _replay_store(self, upto_version: int) -> ArtifactStore:
        """Replay commits into a fresh store, preserving absolute version numbers
        after the compaction baseline."""
        store = ArtifactStore()
        if self._log.baseline_version:
            for artifact_id, data in self._log.replay_state(
                self._log.baseline_version
            ).items():
                store.create(data, artifact_id)
        for commit in self._log.commits_upto(upto_version):
            for op in commit.operations:
                if isinstance(op, Create):
                    created_id = op.artifact_id or op.id
                    if created_id is None:
                        continue
                    if store.current_version(created_id) is None:
                        store.create(op.data, created_id, commit_id=commit.id)
                    else:
                        store.update(created_id, op.data, commit_id=commit.id)
                elif isinstance(op, Update):
                    store.update(op.artifact_id, op.new_data, commit_id=commit.id)
                elif isinstance(op, Delete):
                    store.delete(op.artifact_id)
        return store

    def artifact_version(
        self, artifact_id: str, version: int
    ) -> ArtifactVersion | None:
        """One immutable artifact revision from the version store.

        `version` is the artifact's absolute version counter (0 = create).
        Compaction is authoritative: trimmed versions return `None`. The
        current version resolves the producing commit lazily when the record
        was written before the commit existed (the runtime path).
        """
        artifact = self._artifacts.get(artifact_id)
        record = self._store.version(artifact_id, version)
        if record is None:
            return None
        if (
            record.commit_id is None
            and artifact is not None
            and version == artifact.version
        ):
            producing = self._log.producing_commit(artifact_id)
            if producing is not None:
                return ArtifactVersion(
                    artifact_id=record.artifact_id,
                    version=record.version,
                    data_type=record.data_type,
                    data=record.data,
                    content_hash=record.content_hash,
                    commit_id=producing.id,
                    created_at=producing.timestamp,
                )
        return record

    def version_history(self, artifact_id: str) -> list[ArtifactVersion]:
        """All readable revisions of an artifact, oldest to newest."""
        stored = self._store.history(artifact_id)
        if not stored:
            return []
        versions: list[ArtifactVersion] = []
        for version in range(stored[0].version, stored[-1].version + 1):
            item = self.artifact_version(artifact_id, version)
            if item is not None:
                versions.append(item)
        return versions

    def find_versions(
        self, content_hash: str, *, data_type: str | None = None
    ) -> list[ArtifactVersion]:
        """Every stored revision with this payload hash (content addressing)."""
        return self._store.find_versions(content_hash, data_type)

    def at(self, version: int) -> Context:
        """An immutable snapshot view of this Context at a commit version.

        `self` is not mutated (unlike `checkout`); the returned Context is a
        detached copy with no pending events and no step cursors. Useful for
        reading a historical state without rewinding the live run.
        """
        if not (0 <= version <= self.version):
            raise ValueError(
                f"Invalid snapshot version: {version} (head={self.version})"
            )
        if version < self.compacted_at:
            raise ValueError(
                f"cannot snapshot version {version}: history below the "
                f"compaction baseline ({self.compacted_at}) was discarded"
            )
        snapshot = self.clone()
        snapshot.checkout(version)
        snapshot.replace_events([])
        snapshot._step_cursors = {}
        return snapshot

    def checkout(self, version: int) -> None:
        """Moves head back to a previous version (rollback along the commit chain).

        Artifacts not part of the versioned history (created directly outside
        commits — the "working tree") are preserved.
        """
        if not (0 <= version <= self._log.version):
            raise ValueError(
                f"Invalid checkout version: {version} (head={self._log.version})"
            )
        if version < self._log.baseline_version:
            raise ValueError(
                f"cannot checkout version {version}: history below the "
                f"compaction baseline ({self._log.baseline_version}) was discarded"
            )
        touched: set[str] = set()
        for commit in self._log.commits_from(version):
            for op in commit.operations:
                if isinstance(op, (Create, Update, Delete)) and op.artifact_id:
                    touched.add(op.artifact_id)
        replayed = self._replay_store(version)
        replayed.extend(self._store, ids=set(self._artifacts) - touched)
        self._store = replayed
        self._artifacts = {}
        for artifact_id in replayed._current:
            current = replayed.current_artifact(artifact_id)
            if current is not None:
                self._artifacts[artifact_id] = current

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
        recompute_stale(self)
        reindex_by_type(self)

    def to_dict(self) -> SerializedContext:
        from .serialization import serialize_context

        return serialize_context(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Context:
        from .serialization import deserialize_context

        return cast("Context", deserialize_context(cls, d))

    async def save_checkpoint(self, backend_or_path: str | CheckpointBackend) -> None:
        backend: CheckpointBackend
        if isinstance(backend_or_path, str):
            backend = FileBackend(backend_or_path)
        else:
            backend = backend_or_path
        await backend.save(cast(dict[str, Any], self.to_dict()))

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
        await backend.set(key, cast(dict[str, Any], self.to_dict()))

    @classmethod
    async def from_kv(cls, backend: KVBackend, key: str) -> Context | None:
        """Loads a context previously stored with `to_kv`, or None if absent."""
        data = await backend.get(key)
        return cls.from_dict(data) if data is not None else None

    def __repr__(self) -> str:
        return f"<Context artifacts={len(self._artifacts)} pending_events={len(self._events)}>"
