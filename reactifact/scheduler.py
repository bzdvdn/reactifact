"""reactifact.scheduler — adaptive candidate policy (§26, §24).

A hybrid scheduler for a reactive runtime. It runs on the *candidate* work list
of one iteration and consists of three stages, none of which can starve the run:

1. `filter` — hard, cheap, rule-based **pruning** (may drop candidates that
   don't fit the domain rules at all, so they never reach ranking);
2. `rank` — metric-based **ordering only** (never drops, §26: not every
   scheduling decision needs an LLM);
3. LLM **tie-break** — one structured call when the top candidates are within
   `llm_tie_break` of each other and a model is available (rare, budgeted).

Plus two correctness guards baked in:

- **HITL pin** — any candidate that unblocks an *answered* `PendingQuestion`
  (resume/approval) is forced to the front, so a human approval can never lose
  to ranking (§60);
- **No-starvation fallback** — if filtering would empty the candidate set, the
  policy keeps the original list (the only path to progress must survive).

    runtime = Runtime(ctx, agents=[...], scheduler=uncertainty_policy(
        rules=[not_legacy, not_refuted],
        metric=support_split,
        llm_tie_break=0.05,
    ))
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from .interrupt import PendingQuestion

if TYPE_CHECKING:
    from .agents import Agent
    from .context import Context
    from .events import Event

#: One scheduled candidate: (agent, event, reads).
WorkItem = tuple["Agent", "Event", list[Any]]

Rule = Callable[["Context", "Agent", "Event"], bool]
Metric = Callable[["Context", "Agent", "Event"], float]


def _stable_metric(context: Context, agent: Agent, event: Event) -> float:
    return 0.0


def _is_resume(context: Context, agent: Agent, event: Event) -> bool:
    artifact = context.get(event.artifact_id)
    return isinstance(getattr(artifact, "data", None), PendingQuestion) and bool(
        artifact.data.answered  # type: ignore[union-attr]
    )


DEFAULT_TIE_BREAK_SYSTEM = (
    "You rank which scheduled agent should run FIRST to most reduce "
    "uncertainty about the current question. Reply with the index."
)


class Scheduler:
    """Filter → rank → LLM tie-break; callable from the runtime each iteration.

    `llm_system` is the (overrideable) system prompt for the tie-break call —
    the app can phrase the decision the way its domain wants, or disable it by
    leaving `llm_tie_break=None` (deterministic only).
    """

    def __init__(
        self,
        *,
        rules: Sequence[Rule] = (),
        metric: Metric = _stable_metric,
        llm_tie_break: float | None = None,
        llm_system: str | None = None,
        rank_limit: int | None = None,
    ):
        self.rules = list(rules)
        self.metric = metric
        self.llm_tie_break = llm_tie_break
        self.llm_system = llm_system or DEFAULT_TIE_BREAK_SYSTEM
        #: Optional "choose top-k": keep only the k best *ranked* candidates.
        #: Pinned (HITL-resume) candidates are never trimmed, and the list is
        #: never emptied (at least the top candidate survives) — §24.
        self.rank_limit = rank_limit

    async def __call__(self, context: Context, work: list[WorkItem]) -> list[WorkItem]:
        return await self.run(context, work)

    async def run(self, context: Context, work: list[WorkItem]) -> list[WorkItem]:
        if len(work) <= 1:
            return work

        # pin HITL-resume candidates first (§60); they must never lose to ranking
        pinned: list[WorkItem] = []
        rest: list[WorkItem] = []
        for item in work:
            (pinned if _is_resume(context, item[0], item[1]) else rest).append(item)

        # 1) hard filter (rules) — with no-starvation fallback
        filtered, dropped = [], 0
        for agent, event, reads in rest:
            if all(rule(context, agent, event) for rule in self.rules):
                filtered.append((agent, event, reads))
            else:
                dropped += 1
        if not filtered:
            filtered = rest  # never starve the run (§24)

        # 2) deterministic metric ranking (stable for ties)
        scored = sorted(
            (
                (item, self.metric(context, item[0], item[1]), i)
                for i, item in enumerate(filtered)
            ),
            key=lambda row: (-row[1], row[2]),
        )
        ranked = [row[0] for row in scored]

        # 3) LLM tie-break: only the top pair, only when close and a model exists
        if (
            self.llm_tie_break is not None
            and len(ranked) >= 2
            and context.resources.llm is not None
            and scored[0][1] - scored[1][1] <= self.llm_tie_break
        ):
            ranked = await self._llm_order(context, ranked[:2]) + ranked[2:]

        # 4) optional "choose top-k" — never empties a non-empty ranked list
        if self.rank_limit is not None and len(ranked) > self.rank_limit:
            ranked = ranked[: self.rank_limit]

        return pinned + ranked

    async def _llm_order(self, context: Context, top: list[WorkItem]) -> list[WorkItem]:
        from pydantic import BaseModel

        from .structured import structured_llm

        class _Order(BaseModel):
            first: int

        description = ", ".join(
            f"[{i}] agent={item[0].name} "
            f"capabilities={list(item[0].capabilities)} "
            f"event={item[1].type.value}"
            for i, item in enumerate(top)
        )
        body = await structured_llm(
            context,
            schema=_Order,
            system=self.llm_system,
            user=description,
        )
        if body is None or body.first not in (0, 1):
            return top  # honest fallback: keep the deterministic order
        return [top[body.first], top[1 - body.first]]


def relation_balance_metric(
    *,
    support_relation: str = "supports",
    contradict_relation: str = "contradicts",
    support_weight: float = 1.0,
    contradict_weight: float = 1.0,
) -> Metric:
    """Built-in uncertainty proxy (§26): ranks a candidate by how lopsided
    the evidence is for the artifact its event is about — the same
    structural signal `examples/medic_lab` computes by hand (§35-36). More
    contradictions relative to support raises the score (higher priority to
    re-investigate); well-supported artifacts sink toward the bottom.

    Fully deterministic and provider-agnostic — reads `Link` relations
    already recorded via `effects.link(...)` (`Context.incoming`), never
    asks the model. Use `llm_tie_break` on `Scheduler`/`uncertainty_policy`
    on top of this if you also want a model to break close ties.
    """

    def metric(context: Context, agent: Agent, event: Event) -> float:
        artifact_id = event.artifact_id
        if artifact_id is None:
            return 0.0
        n_support = len(context.incoming(artifact_id, relation=support_relation))
        n_contra = len(context.incoming(artifact_id, relation=contradict_relation))
        return contradict_weight * n_contra - support_weight * n_support

    return metric


def uncertainty_policy(
    *,
    rules: Sequence[Rule] = (),
    metric: Metric = _stable_metric,
    llm_tie_break: float | None = None,
    llm_system: str | None = None,
    rank_limit: int | None = None,
) -> Scheduler:
    """The built-in hybrid policy (filter → rank → LLM tie-break → top-k)."""
    return Scheduler(
        rules=rules,
        metric=metric,
        llm_tie_break=llm_tie_break,
        llm_system=llm_system,
        rank_limit=rank_limit,
    )


__all__ = [
    "DEFAULT_TIE_BREAK_SYSTEM",
    "Metric",
    "Rule",
    "Scheduler",
    "WorkItem",
    "relation_balance_metric",
    "uncertainty_policy",
]
