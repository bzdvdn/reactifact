# Branching & merge (§39-§40)

> Live walkthrough: [`examples/forklab`](https://github.com/bzdvdn/reactifact/tree/master/examples/forklab) —
> a deterministic two-strategy fork → merge → evaluate demo, with a `--conflict`
> flag showing an explicit conflict and a policy resolving it.

State, not orchestration: a fork is an **independent copy of the context** for
exploring an alternative state — the hypotheses of §39 are forks, not paths in
an execution graph.

## Fork

```python
from reactifact import Context, RuntimeResources

base = Context(resources=RuntimeResources())
base.create(Note(text="v1"), id="note:1")

hypothesis_a = base.branch(name="hypothesis-a")
hypothesis_b = base.branch(name="hypothesis-b")

# both are fully isolated from now on
hypothesis_a.create(...)
hypothesis_b.create(...)
```

`branch()` deep-copies the state **and records a snapshot of the base**, so a
later `merge` of two fork-mates can detect divergence three-way. The branch
shares `resources` with the parent but diverges in every artifact/relation.

## Merge — explicit conflicts, never a silent choice (§40)

```python
hypothesis_a.merge(hypothesis_b)
```

Three-way merge against the shared fork base. For each artifact present in
base/self/other:

| Case | Result |
| --- | --- |
| `self == other` | no-op |
| `self == base` (only the other moved it) | adopt `other` |
| `other == base` (only self moved it) | keep `self` |
| otherwise (both diverged differently) | **`MergeConflict`**, nothing applied |

Merging is **atomic**: a single conflicting artifact aborts the whole merge, so
no partial state is ever left behind. A merge that succeeds is logged as a
`merge` commit. Deletions participate too — one side deleting an artifact the
other side edited is a conflict; a clean delete propagates.

```python
from reactifact import MergeConflict

try:
    hypothesis_a.merge(hypothesis_b)
except MergeConflict as exc:
    print(exc.conflicts)     # e.g. ["note:1 diverged since the fork (self=changed, other=changed)"]
```

The conflict list is the input for a verifier/merge policy — the framework does
not choose (§40). When a type has a deterministic resolution, opt in per type:

```python
left.merge(
    right,
    policies={Note: lambda a, b: Note(text=f"{a.text}\n{b.text}")},
)
```

A policy receives the target and incoming data and must return the same model
type; without one, the merge stays fail-loud. Policies are explicit and
auditable — the merge commit records the resolved value.

### Optimistic writes, not last-write-wins

Parallel agents in one generation share a snapshot; a plain `context.update`
would silently keep the last write. Mark the version you read instead:

```python
effects.update(note, expected_version=note.version, text="new")
# raises ArtifactConflict if another writer moved `note`
```

The runtime validates every `expected_version` in a generation **before**
applying any patch, so a conflict leaves the generation untouched. Re-run the
turn (or let a `RetryPolicy` handle the error, if you model it as one) and the
agent reads the new version.

## Persistence: `BranchStore` on top of the KV backend

Branches survive restarts as named keys over the **same KV backend** — no new
storage, the semantics live in `Context` operations:

```python
from reactifact.branching import BranchStore
from reactifact.checkpoints import SQLiteKVBackend

store = BranchStore(SQLiteKVBackend("sessions.sqlite3"))
await store.save_branch(hypothesis_a, session_id="demo", name="hypothesis-a")
restored = await store.load_branch("demo", "hypothesis-a")
restored.merge(await store.load_branch("demo", "hypothesis-b"))   # base survives too
```

The fork base snapshot is serialized with the context, so `merge` keeps its
conflict detection after a reload. On SQLite/Postgres, `BranchStore` uses the
same snapshot + append-only commit log as `SessionStore` (per branch key), with
the same `compact_every` bound.

## CLI

```bash
python -m reactifact branch sessions.sqlite3 demo list
python -m reactifact branch sessions.sqlite3 demo save hypothesis-a
python -m reactifact branch sessions.sqlite3 demo merge --into a --source b --as merged
```

## When to use which

| Situation | Approach |
| --- | --- |
| An artifact moves through phase states | `StatusMachine` + verify-produce |
| A workflow needs to roll *back* on user edits | stage guard + downstream resets |
| **Alternative states to explore & compare** | `branch()` + `merge()` (§39-§40) |
| "Which of these did the model pick?" | parse + guard (like `PickStage`) |