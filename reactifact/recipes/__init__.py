"""Off-the-shelf building blocks for agents — reactive patterns and helpers.

These are not core primitives (Context/Artifact/Patch/Produce stay minimal, the
constitution's primitives-first rule). Instead they are ready-made patterns that
keep reappearing across agent codebases and the bundled examples:

- `find` / `find_all` — pick the typed artifact(s) out of a produce's
  `inputs` without repeating `next(... isinstance ...)` — see `recipes.inputs`;
- `EphemeralCleanup` / `PrefixedEphemeralCleanup` — delete a turn's/thread's
  "scratch" artifacts once a terminal artifact exists for their shared
  correlation key, instead of every app hand-rolling the same
  `Produce[Terminal]` subclass (§24, §42) — see `recipes.cleanup`;
  `SeedIdentity` — per-request data (an authenticated user, a tenant id, ...)
  into a turn via a `contextvars.ContextVar` set right before
  `ChatAssistant.stream()`/`.invoke()`, written as an ordinary artifact
  instead of a `resources`-side dict (§24, §42) — see `recipes.identity`;
- `fan_out_sources` — query all configured sources, emit ranked, idempotent
  `SourceRef`s tagged with an owner (§8, §24, §42) — see `recipes.search`;
- `materialize_doc` — lazily resolve a `SourceRef` into a document with a
  provenance edge (Reference → Artifact, §6, §34) — see `recipes.resolve`;
- `StatusMachine` — a `Produce` that deterministically advances an artifact's
  `status` lifecycle driven by a pure `next_status(context, key)` (§67, §69) —
  see `recipes.status`;
- `PlanExecute` — sequential plan → execute → finish: ordering, gating each
  step on its predecessor's result, and completion detection owned by the
  recipe; domain supplies `plan`/`execute_step`/`finish` (§24, §42, §69) —
  see `recipes.plan_execute`;
- `Router` / `ApprovalGate` — classify a request into a route with a
  deterministic fallback, and ask a human to sign off on a report before
  finalizing it (§60, §67) — see `recipes.supervisor`;
- `ReflectionLoop` — generate → critique → regenerate: round-capping, the
  accept threshold, and completion detection owned by the recipe; domain
  supplies `draft`/`critique`/`rewrite`/`finish` (§24, §42, §69) — see
  `recipes.reflection`;
- `WindowSummarizer` / `WindowPruner` / `llm_summarizer` — bounded
  conversation memory: periodic summarization + pruning as two plain
  `Produce`s, domain owns the summarizer callback and the summary artifact
  shape (§27, §37) — see `recipes.memory`;
- `RollingDigestSummarizer` / `llm_digest_summarizer` — the other bounded-
  memory shape: one growing digest that folds in whatever falls out of the
  raw window (instead of `WindowSummarizer`'s per-round checkpoints), and
  prunes the folded messages itself (§27, §37) — see `recipes.memory`;
- `keyword_score` / `stem_words` — deterministic text scoring without
  embedders (English and Russian) — see `recipes.text`;
- `changed_fields` / `earliest_stage` / `downstream_fields` — the
  "change → rebuild" model for multi-stage flows — see `recipes.rollback`;
- `Skill` / `load_skills` / `match_skills` — keyword-triggered instruction
  snippets (Claude-Skills-shaped: name/description frontmatter + body) loaded
  into a prompt when their description matches the situation — see
  `recipes.skills`.

Deterministic where it can be, LLM-free by design; expose the domain hook.
Extend by adding a module here (the package stays import-surface-flat).
"""

from __future__ import annotations

from .cleanup import EphemeralCleanup, PrefixedEphemeralCleanup
from .identity import SeedIdentity
from .inputs import find, find_all
from .memory import (
    RollingDigestSummarizer,
    WindowPruner,
    WindowSummarizer,
    llm_digest_summarizer,
    llm_summarizer,
)
from .plan_execute import PlanExecute
from .reflection import ReflectionLoop
from .resolve import materialize_doc
from .rollback import changed_fields, downstream_fields, earliest_stage
from .search import fan_out_sources
from .skills import Skill, load_skills, match_skills
from .status import StatusMachine
from .supervisor import ApprovalGate, Router
from .text import EN_STOPWORDS, keyword_score, stem, stem_words

__all__ = [
    "ApprovalGate",
    "EN_STOPWORDS",
    "EphemeralCleanup",
    "PlanExecute",
    "PrefixedEphemeralCleanup",
    "ReflectionLoop",
    "RollingDigestSummarizer",
    "Router",
    "SeedIdentity",
    "Skill",
    "StatusMachine",
    "WindowPruner",
    "WindowSummarizer",
    "changed_fields",
    "downstream_fields",
    "earliest_stage",
    "fan_out_sources",
    "find",
    "find_all",
    "keyword_score",
    "llm_digest_summarizer",
    "llm_summarizer",
    "load_skills",
    "match_skills",
    "materialize_doc",
    "stem",
    "stem_words",
]
