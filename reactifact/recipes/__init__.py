"""Off-the-shelf building blocks for agents — reactive patterns and helpers.

These are not core primitives (Context/Artifact/Patch/Step stay minimal, the
constitution's primitives-first rule). Instead they are ready-made patterns that
keep reappearing across agent codebases and the bundled examples:

- `find` / `find_all` — pick the typed artifact(s) out of a produce's
  `inputs` without repeating `next(... isinstance ...)` — see `recipes.inputs`;
- `EphemeralCleanup` / `PrefixedEphemeralCleanup` — delete a turn's/thread's
  "scratch" artifacts once a terminal artifact exists for their shared
  correlation key, instead of every app hand-rolling the same
  `Step[Terminal]` subclass (§24, §42) — see `recipes.cleanup`;
  `SeedIdentity` — per-request data (an authenticated user, a tenant id, ...)
  into a turn via a `contextvars.ContextVar` set right before
  `Chat.stream()`/`.invoke()`, written as an ordinary artifact
  instead of a `resources`-side dict (§24, §42) — see `recipes.identity`;
- `fan_out_sources` — query all configured sources, emit ranked, idempotent
  `SourceRef`s tagged with an owner; `as_artifacts=True` returns the planned
  handles (§8, §24, §42) — see `recipes.search`;
- `materialize_doc` — lazily resolve a `SourceRef`/artifact/handle into a
  document with a provenance edge; `doc_type=`, `refresh=`, `on_error=` keep
  the simple case short (§6, §34) — see `recipes.resolve`;
- `StatusMachine` — a step that deterministically advances an artifact's
  `status` lifecycle driven by a pure `next_status(state)` over key-scoped
  inputs; subclass it or use `from_funcs(...)` (§67, §69) — see
  `recipes.status`;
- `PlanExecute` — sequential plan → execute → finish: ordering, gating each
  step on its predecessor's result, and completion detection owned by the
  recipe; domain supplies `plan`/`execute_step`/`finish` (§24, §42, §69) —
  see `recipes.plan_execute`;
- `Router` / `ApprovalGate` — classify a request into a route with a
  deterministic fallback, and ask a human to sign off on a report before
  finalizing it (§60, §67) — see `recipes.supervisor`;
- `ApprovalIntent` / `ApprovalDecision` / `classify_approval` /
  `InterpretApproval` / `ClarifyApproval` — turn a free-text human approval into
  an explicit, auditable decision (typed yes/no first, LLM only as a fallback,
  ambiguity never auto-approves) — see `recipes.approval`;
- `ReflectionLoop` — generate → critique → regenerate: round-capping, the
  accept threshold, and completion detection owned by the recipe; domain
  supplies `draft`/`critique`/`rewrite`/`finish` (§24, §42, §69) — see
  `recipes.reflection`;
- `WindowSummarizer` / `WindowPruner` / `llm_summarizer` — bounded
  conversation memory: periodic summarization + pruning as two plain
  steps, domain owns the summarizer callback and the summary artifact
  shape (§27, §37) — see `recipes.memory`;
- `Conversation` / `ConversationMessage` / `add_message` / `create_turn` /
  `transcript` — a canonical multi-turn conversation (an append helper + a
  renderer to provider `Message`s), generic over *your* message model (any
  model with `role`/`text`; `ConversationMessage` is the default). For an app
  that already has separate question/answer types, `Transcript` + `MessageSpec`
  render several typed turns into one `Message` list instead (§27, §37) — see
  `recipes.conversation`;
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
  `recipes.skills`;
- `map_reduce` / `MapTask` / `MapResult` / `MapReduced` — fan out independent
  map work, then reduce once all results are in — see `recipes.map_reduce`;
- `swarm` / `Handoff` — peer-to-peer handoff loop with a terminal answer or a
  bounded cycle — see `recipes.swarm`;
- `run_tool_loop` / `ToolLoopResult` — an opt-in native tool-calling loop over
  `reactifact.tools.native`, with mandatory-tool nudges and observations — see
  `recipes.tool_loop`.

Deterministic where it can be, LLM-free by design; expose the domain hook.
Extend by adding a module here (the package stays import-surface-flat).
"""

from __future__ import annotations

from ..tools.native import (
    looks_like_tool_markup,
    parse_textual_tool_calls,
    parse_tool_calls_with_textual,
)
from .approval import (
    Approval,
    ApprovalDecision,
    ApprovalFlow,
    ApprovalIntent,
    ApprovalPolicy,
    ClarifyApproval,
    InterpretApproval,
    classify_approval,
)
from .cleanup import EphemeralCleanup, PrefixedEphemeralCleanup, ScopedCleanup
from .conversation import (
    Conversation,
    ConversationMessage,
    MessageSpec,
    Transcript,
    TurnContext,
    TurnContextStep,
    TurnFacts,
    add_message,
    conversation_state,
    create_turn,
    looks_like_follow_up,
    transcript,
    turn_count,
)
from .identity import SeedIdentity
from .inputs import correlation_id_of, find, find_all
from .map_reduce import (
    MapPlan,
    MapReduced,
    MapReduceResult,
    MapResult,
    MapTask,
    map_reduce,
)
from .memory import (
    RollingDigestSummarizer,
    WindowPruner,
    WindowSummarizer,
    llm_digest_summarizer,
    llm_summarizer,
)
from .memory_store import MemoryHit, MemoryRecord, MemoryStore
from .plan_execute import PlanExecute
from .reflection import ReflectionLoop
from .resolve import materialize_doc
from .rollback import changed_fields, downstream_fields, earliest_stage
from .search import fan_out_sources, search_sources, source_ref_id
from .skills import Skill, load_skills, match_skills
from .status import StatusMachine, StatusState
from .supervisor import ApprovalGate, Router
from .swarm import Handoff, SwarmCycle, SwarmError, SwarmLimit, SwarmResult, swarm
from .text import EN_STOPWORDS, keyword_score, stem, stem_words
from .tool_loop import ToolLoopResult, ToolObservation, run_tool_loop

__all__ = [
    # approval
    "Approval",
    "ApprovalDecision",
    "ApprovalFlow",
    "ApprovalGate",
    "ApprovalIntent",
    "ApprovalPolicy",
    "ClarifyApproval",
    "InterpretApproval",
    "classify_approval",
    # inputs, cleanup, identity
    "SeedIdentity",
    "EphemeralCleanup",
    "PrefixedEphemeralCleanup",
    "ScopedCleanup",
    "correlation_id_of",
    "find",
    "find_all",
    # search / resolve
    "fan_out_sources",
    "materialize_doc",
    "search_sources",
    "source_ref_id",
    # status / rollback
    "StatusMachine",
    "StatusState",
    "changed_fields",
    "downstream_fields",
    "earliest_stage",
    # plan / reflection / routing
    "PlanExecute",
    "ReflectionLoop",
    "Router",
    # memory / conversation
    "Conversation",
    "ConversationMessage",
    "MessageSpec",
    "MemoryHit",
    "MemoryRecord",
    "MemoryStore",
    "RollingDigestSummarizer",
    "Transcript",
    "TurnContext",
    "TurnContextStep",
    "TurnFacts",
    "looks_like_follow_up",
    "WindowPruner",
    "WindowSummarizer",
    "add_message",
    "conversation_state",
    "create_turn",
    "llm_digest_summarizer",
    "llm_summarizer",
    "transcript",
    "turn_count",
    # map-reduce / swarm
    "Handoff",
    "MapPlan",
    "MapReduceResult",
    "MapReduced",
    "MapResult",
    "MapTask",
    "SwarmCycle",
    "SwarmError",
    "SwarmLimit",
    "SwarmResult",
    "map_reduce",
    "swarm",
    # text / skills
    "EN_STOPWORDS",
    "Skill",
    "keyword_score",
    "load_skills",
    "match_skills",
    "stem",
    "stem_words",
    # tool loop
    "ToolLoopResult",
    "ToolObservation",
    "looks_like_tool_markup",
    "parse_textual_tool_calls",
    "parse_tool_calls_with_textual",
    "run_tool_loop",
]
