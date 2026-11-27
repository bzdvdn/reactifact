"""forklab pipeline — application-level orchestration (§39-§40).

The single place that decides *how* the demo runs: prepare the forks, run each
in its own `Runtime`, merge them three-way (§40), evaluate the merged state.
Both entry points — the CLI `main` and the SSE `web` — import from here, so
orchestration is not entry code and entries are not orchestrators.
"""

from __future__ import annotations

import asyncio
from typing import Any

from reactifact import (
    Budget,
    Context,
    MergeConflict,
    Runtime,
    RuntimeResources,
)
from reactifact.providers import LLMProvider

from .agents import EVALUATE, INVESTIGATE
from .models import (
    Answer,
    Evidence,
    Question,
    Review,
    Strategy,
)
from .models import (
    Budget as ArtifactBudget,
)


def _new_context(llm: LLMProvider | None) -> Context:
    return Context(resources=RuntimeResources(llm=llm))


def _arun(runtime: Runtime) -> None:
    asyncio.run(runtime.arun())


def make_fork(
    question: str,
    topic: str,
    *,
    name: str,
    kind: str,
    llm: LLMProvider | None,
) -> Context:
    """Prepares a fork: question + shared budget + the strategy trigger (not run)."""
    base = _new_context(llm)
    base.create(Question(text=question, topic=topic))
    base.create(ArtifactBudget(tokens=100), id="budget:1")
    fork = base.branch(name=name)
    fork.create(Strategy(branch=name, kind=kind))
    return fork


def investigate_runtime(fork: Context) -> Runtime:
    """The per-fork runtime (run or `astream` — the web app streams it)."""
    return Runtime(fork, agents=INVESTIGATE.agents, budget=Budget(max_runs=5))


def merge_forks(depth: Context, breadth: Context) -> None:
    """Three-way merge of the two finished forks (conflicts explicit, §40)."""
    depth.merge(breadth)


def evaluate_runtime(merged: Context) -> Runtime:
    """The merged-state runtime: `Review` triggers the evaluator → Answer."""
    merged.create(Review(base_on="merged"))
    return Runtime(merged, agents=EVALUATE.agents, budget=Budget(max_runs=5))


def _merge_with_policy(depth: Context, breadth: Context) -> None:
    """§40 must not choose silently: show a real conflict, then a policy resolves it.

    Both forks edit the shared `budget:1` artifact; the merge raises
    `MergeConflict` and applies nothing (atomic). A policy — "keep depth" —
    resets the breadth edit back to the base value and re-merges cleanly.
    """
    depth.update("budget:1", ArtifactBudget(tokens=120))
    breadth.update("budget:1", ArtifactBudget(tokens=60))
    try:
        depth.merge(breadth)
    except MergeConflict as exc:
        print(f"[conflict] {exc.conflicts[0]}")
        print("[policy] keeping the depth fork's budget; re-merging without that edit")
        breadth.update("budget:1", ArtifactBudget(tokens=100))
        depth.merge(breadth)


def run(
    *,
    question: str = "Which design recovers the most thermal energy?",
    topic: str = "thermal energy recovery in HVAC design",
    conflict: bool = False,
    llm: LLMProvider | None = None,
) -> Context:
    """Executes the fork → investigate → merge → evaluate pipeline (§39-§40).

    Pass `llm` to exercise the wording/synthesis calls (deterministic fallback
    otherwise, §59). `topic` is the domain context every model prompt carries (§68).
    Returns the final (merged) context; the answer lives at `answer:merged`.
    """
    depth = make_fork(question, topic, name="depth", kind="depth", llm=llm)
    breadth = make_fork(question, topic, name="breadth", kind="breadth", llm=llm)
    _arun(investigate_runtime(depth))
    _arun(investigate_runtime(breadth))

    if conflict:
        _merge_with_policy(depth, breadth)
    else:
        merge_forks(depth, breadth)

    _arun(evaluate_runtime(depth))
    return depth


def result_data(merged: Context) -> dict[str, Any]:
    """Presentation-ready payload built from the merged context (web + CLI)."""
    answers = merged.list_artifacts(Answer)
    answer = answers[0] if answers else None
    splits: dict[str, int] = {}
    for evidence in merged.list_artifacts(Evidence):
        splits[evidence.data.branch] = splits.get(evidence.data.branch, 0) + 1
    return {
        "answer": answer.data.text if answer is not None else "",
        "sources": answer.data.sources if answer is not None else [],
        "splits": splits,
        "version": merged.version,
    }


__all__ = [
    "evaluate_runtime",
    "investigate_runtime",
    "make_fork",
    "merge_forks",
    "result_data",
    "run",
]
