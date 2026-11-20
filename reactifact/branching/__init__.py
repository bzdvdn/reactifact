"""Fork/merge operations plus durable branch persistence (§39-§40)."""

from .ops import (
    MergeConflict,
    MergePolicy,
    clone_context,
    fork_context,
    merge_context_from,
    merge_contexts,
)
from .store import BranchStore

__all__ = [
    "BranchStore",
    "MergeConflict",
    "MergePolicy",
    "clone_context",
    "fork_context",
    "merge_context_from",
    "merge_contexts",
]
