from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict, TypeVar, cast, overload

from pydantic import BaseModel

from .artifacts import Artifact, ArtifactVersion, artifact_content_hash
from .checkpoints import CheckpointBackend, FileBackend, KVBackend
from .commit import Commit
from .commit_log import CommitLog
from .events import Event, EventType
from .interrupt import PendingAction, PendingQuestion
from .patches import (
    Create,
    Delete,
    Link,
    Relation,
    Unlink,
    Update,
)
from .relations import RelationGraph
from .resources import RuntimeResources
from .streaming import EventHub, ProgressEvent, QueueEvent
from .types import resolve, type_id_of

TData = TypeVar("TData", bound=BaseModel)
TArtifact = TypeVar("TArtifact", bound=BaseModel)

#: Current persisted-envelope version. Bump when `to_dict`'s shape changes and
#: register a migration in `_CONTEXT_MIGRATIONS` for the previous version.
CONTEXT_FORMAT_VERSION = 1


class SerializedContext(TypedDict, total=False):
    """The persisted Context envelope (`Context.to_dict`).

    `total=False` because older payloads predate some keys (notably
    `format_version`, treated as v1 by `migrate_context_payload`).
    """

    format_version: int
    version: int
    head_id: str | None
    artifacts: dict[str, dict[str, Any]]
    relations: list[dict[str, Any]]
    commits: list[dict[str, Any]]
    commit_baseline: dict[str, Any] | None
    events: list[dict[str, Any]]
    fork_name: str
    base: SerializedContext | None
    step_cursors: dict[str, dict[str, dict[str, int]]]


#: from_version -> migration that upgrades the payload by one version.
_CONTEXT_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


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
        from .request import current_request

        return current_request()

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

    # ---- Outbox: external side effects awaiting dispatch ----

    def pending_actions(self) -> list[Artifact[PendingAction]]:
        """Committed outbound actions the dispatcher has not performed yet (§42)."""
        return [
            a for a in self.list_artifacts(PendingAction) if a.data.status == "pending"
        ]

    def mark_dispatched(self, action_id: str) -> Artifact[PendingAction] | None:
        """Records that an outbound action was performed (a regular patch).

        Called by the runtime after the dispatcher returns; the `dispatched`
        status is what stops a later generation or a replay from re-sending it.
        """
        artifact = self._artifacts.get(action_id)
        if artifact is None or not isinstance(artifact.data, PendingAction):
            return None
        updated = artifact.data.model_copy(
            update={
                "status": "dispatched",
                "dispatched_at": datetime.now(UTC),
                "error": None,
            }
        )
        return self.update(action_id, updated)

    def mark_failed(
        self, action_id: str, error: str | None = None
    ) -> Artifact[PendingAction] | None:
        """Records a failed dispatch (terminal; `retry_action` re-arms it).

        A failing dispatcher propagates out of `arun()` (fail-loud, §69); this
        keeps the failure visible as state (§59) rather than a bare exception,
        and bumps `attempts` so a repeated failure is visible.
        """
        artifact = self._artifacts.get(action_id)
        if artifact is None or not isinstance(artifact.data, PendingAction):
            return None
        updated = artifact.data.model_copy(
            update={
                "status": "failed",
                "error": error,
                "attempts": artifact.data.attempts + 1,
            }
        )
        return self.update(action_id, updated)

    def retry_action(self, action_id: str) -> Artifact[PendingAction] | None:
        """Re-arms a `failed` action as `pending` (resetting `attempts`).

        The retry policy is the application's (or the dispatcher's) — this just
        puts the action back in the outbox so the next drain tries it again.
        """
        artifact = self._artifacts.get(action_id)
        if artifact is None or not isinstance(artifact.data, PendingAction):
            return None
        updated = artifact.data.model_copy(
            update={"status": "pending", "error": None, "attempts": 0}
        )
        return self.update(action_id, updated)

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
                    existing.update(op.data)
                    self._by_type.setdefault(type(op.data), set()).add(artifact_id)
                else:
                    self._artifacts[artifact_id] = Artifact(
                        op.data, id=artifact_id, created_by_commit=commit.id
                    )
                    self._by_type.setdefault(type(op.data), set()).add(artifact_id)
            elif isinstance(op, Update):
                artifact = self._artifacts.get(op.artifact_id)
                if artifact is None:
                    continue
                old_type = type(artifact.data)
                artifact.update(op.new_data)
                new_type = type(op.new_data)
                if new_type is not old_type:
                    self._by_type.get(old_type, set()).discard(op.artifact_id)
                    self._by_type.setdefault(new_type, set()).add(op.artifact_id)
            elif isinstance(op, Delete):
                artifact = self._artifacts.pop(op.artifact_id, None)
                if artifact is not None:
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
        self._recompute_stale()
        self._reindex_by_type()

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
            for artifact in self._artifacts.values():
                artifact.trim_history(keep_versions)
        if dropped:
            self._recompute_stale()
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

    def artifact_version(
        self, artifact_id: str, version: int
    ) -> ArtifactVersion | None:
        """One immutable artifact revision, reconstructed from commits when needed.

        `version` is the artifact's absolute version counter (0 = create). The
        result carries the payload, its sha256 and the commit that wrote it —
        the content-addressed view of a revision, even after `_history` was
        trimmed (as long as the commit log still has it).
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return None
        commit: Commit | None = None
        data: BaseModel | None = None
        if version == artifact.version:
            data = artifact.data
            commit = self._log.producing_commit(artifact_id)
        else:
            current = -1
            for candidate in self._log.history():
                for op in candidate.operations:
                    if (
                        isinstance(op, Create)
                        and (op.artifact_id or op.id) == artifact_id
                    ):
                        current += 1
                        candidate_data = op.data
                    elif isinstance(op, Update) and op.artifact_id == artifact_id:
                        current += 1
                        candidate_data = op.new_data
                    else:
                        continue
                    if current == version:
                        data = candidate_data
                        commit = candidate
                        break
                if data is not None:
                    break
            if data is None:
                try:
                    data = artifact.at(version)
                except ValueError:
                    return None
        if data is None:
            return None
        return ArtifactVersion(
            artifact_id=artifact_id,
            version=version,
            data_type=type_id_of(type(data)),
            data=data,
            content_hash=artifact_content_hash(data),
            commit_id=commit.id if commit is not None else None,
            created_at=commit.timestamp if commit is not None else None,
        )

    def version_history(self, artifact_id: str) -> list[ArtifactVersion]:
        """All readable revisions of an artifact, oldest to newest."""
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return []
        versions: list[ArtifactVersion] = []
        for version in range(artifact.version + 1):
            item = self.artifact_version(artifact_id, version)
            if item is not None:
                versions.append(item)
        return versions

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

    def _serialize_step_cursors(self) -> dict[str, dict[str, dict[str, int]]]:
        """Serializes watermark cursors with stable type ids (JSON-safe keys)."""
        payload: dict[str, dict[str, dict[str, int]]] = {}
        for (agent, step, scope), cursor in self._step_cursors.items():
            key = json.dumps([agent, step, scope], ensure_ascii=False, default=str)
            payload[key] = {
                type_id_of(artifact_type): dict(vector)
                for artifact_type, vector in cursor.items()
            }
        return payload

    @staticmethod
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
                    # 0.2 preview shape: [latest id, max version]
                    vector = {str(raw[0]): int(raw[1])}
                else:
                    continue
                cursor[artifact_type] = vector
            cursors[(agent, step, scope)] = cursor
        return cursors

    def to_dict(self) -> SerializedContext:
        return {
            "format_version": CONTEXT_FORMAT_VERSION,
            "version": self._log.version,
            "head_id": self._log.head_id,
            "artifacts": {aid: art.to_dict() for aid, art in self._artifacts.items()},
            "relations": self._relations.to_dict(),
            "commits": self._log.to_dict(),
            "commit_baseline": self._log.to_baseline_dict(),
            # Pending triggers, so an interrupted run resumes after a process
            # restart instead of settling on an empty queue. `context_hash`
            # deliberately excludes these (state, not provenance): the queue
            # carries no artifact content, only "what still needs reacting to".
            "events": [event.to_dict() for event in self._events],
            "fork_name": self._fork_name,
            "base": self._base.to_dict() if self._base is not None else None,
            "step_cursors": self._serialize_step_cursors(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Context:
        data = migrate_context_payload(dict(d))
        ws = cls()
        for aid, art_dict in data["artifacts"].items():
            artifact = Artifact.from_dict(art_dict)
            ws._artifacts[aid] = artifact
        ws._relations = RelationGraph.from_dict(data.get("relations", []))
        ws._log = CommitLog.from_dict(
            data["commits"],
            version=data.get("version"),
            head_id=data.get("head_id"),
            baseline=data.get("commit_baseline"),
        )
        ws._events = [Event.from_dict(e) for e in data.get("events", [])]
        ws._fork_name = data.get("fork_name", "")
        ws._base = (
            Context.from_dict(data["base"]) if data.get("base") is not None else None
        )
        ws._step_cursors = cls._deserialize_step_cursors(data.get("step_cursors", {}))
        ws._recompute_stale()
        ws._reindex_by_type()
        ws.mark_persisted(ws.version)
        return ws

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
