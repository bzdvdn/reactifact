"""incident_commander pipeline — application-level orchestration.

The single place that decides *how* the demo runs: fork the investigation,
run each side, merge, decide + remediate (gated), verify. The CLI entry
(`main.py`) only parses arguments and prints — orchestration is not entry
code, and entries are not orchestrators (same split as `examples/forklab`).
"""

from __future__ import annotations

from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.context_builder import TokenBudgetContextBuilder
from reactifact.providers import LLMProvider

from .agents import build_ask_dba, build_commander, build_verifier
from .fake_llm import COMMANDER_SCRIPT, DBA_SCRIPT, ScriptedLLM
from .models import IncidentReport, InvestigationComplete, InvestigationTask
from .produce import (
    AnswerAgent,
    K8sInvestigatorAgent,
    SynthesizerAgent,
    build_db_investigator_agent,
)

DEFAULT_INCIDENT = (
    "checkout-api pods are CrashLoopBackOff since the last deploy; customers "
    "can't complete payment, and the team suspects database timeouts too."
)

#: Deterministic classification (§67) — which places an incident plausibly
#: touches. Keyword overlap, no LLM: the same "cheap, rule-based filter
#: before anything generative" idiom `recipes.Router.fallback_route` uses.
TARGET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "k8s": ("pod", "crashloop", "k8s", "kubernetes", "deploy", "container", "restart"),
    "db": ("database", "db ", "query", "lock", "timeout", "sql", "postgres", "pool"),
}


def classify_targets(incident_text: str) -> set[str]:
    """Which forks this incident actually needs — not "always both" (§39
    is for state exploration that's actually needed, not a fixed ritual).
    Never dead-ends (§59): text matching neither keyword set investigates
    everywhere rather than silently skipping a place that might matter."""
    low = incident_text.lower()
    targets = {
        target for target, kws in TARGET_KEYWORDS.items() if any(k in low for k in kws)
    }
    return targets or set(TARGET_KEYWORDS)


def build_resources(llm: LLMProvider, context_max_tokens: int) -> RuntimeResources:
    return RuntimeResources(
        llm=llm,
        context_builder=TokenBudgetContextBuilder(
            max_tokens=context_max_tokens,
            # `SynthesizerAgent` also consumes `InvestigationComplete` (its
            # guaranteed post-merge wake-up — `Context.merge()`, unlike
            # `merge_from()`, doesn't emit events for merged-in artifacts,
            # see `InvestigationComplete`'s docstring). `exempt_types` keeps
            # that marker free: it still wakes the agent, but never
            # competes with `Evidence` for the token budget (or, ranked
            # first as the newest artifact, blocks `Evidence` out
            # entirely — see `exempt_types`'s docstring).
            exempt_types=(InvestigationComplete,),
        ),
        verification_threshold=0.75,
    )


def make_investigation_forks(
    incident_text: str, resources: RuntimeResources, targets: set[str]
) -> tuple[str, dict[str, Context]]:
    """One shared base (so `IncidentReport` keeps the same id everywhere),
    forked into one independent investigation branch per relevant target
    (§39) — not unconditionally both; `classify_targets` decides which."""
    base = Context(resources=resources)
    incident = base.create(IncidentReport(text=incident_text))
    forks: dict[str, Context] = {}
    for target in targets:
        fork = base.branch(name=target)
        fork.create(InvestigationTask(incident_id=incident.id, target=target))
        forks[target] = fork
    return incident.id, forks


async def investigate(forks: dict[str, Context], dba_llm: LLMProvider) -> None:
    """Runs each relevant fork's investigation to completion, independently
    — only the forks `classify_targets` actually produced."""
    if "k8s" in forks:
        await Runtime(
            forks["k8s"], agents=[K8sInvestigatorAgent()], budget=Budget(max_runs=20)
        ).arun()
    if "db" in forks:
        ask_dba = build_ask_dba(RuntimeResources(llm=dba_llm))
        await Runtime(
            forks["db"],
            agents=[build_db_investigator_agent(ask_dba)],
            budget=Budget(max_runs=20),
        ).arun()


def merge_investigation(
    forks: dict[str, Context], incident_id: str, incident_text: str
) -> Context:
    """Three-way merge, one timeline again (§40) — with a single fork
    (one relevant target), there's nothing to merge, the fork itself just
    becomes the timeline. Then the explicit wake-up for the decision phase
    (see `InvestigationComplete`)."""
    targets = list(forks)
    ctx = forks[targets[0]]
    for other in targets[1:]:
        ctx.merge(forks[other], message=f"merged {' + '.join(targets)} investigation")
    ctx.create(InvestigationComplete(incident_id=incident_id, text=incident_text))
    return ctx


def decision_runtime(ctx: Context) -> Runtime:
    """Decide + remediate (gated) → build the Answer → verify it."""
    return Runtime(
        ctx,
        agents=[build_commander(), SynthesizerAgent(), AnswerAgent(), build_verifier()],
        budget=Budget(max_runs=200, max_tool_calls=12),
    )


async def run(
    *,
    incident_text: str = DEFAULT_INCIDENT,
    llm: LLMProvider | None = None,
    context_max_tokens: int = 50,
) -> Context:
    """Executes the classify → fork → investigate → merge → decide/gate →
    verify pipeline. Pass `llm` for a live model (every conversation shares
    it); offline, each independent conversation gets its own scripted
    response queue (`fake_llm.py`). Returns the final context."""
    commander_llm = llm or ScriptedLLM(COMMANDER_SCRIPT)
    dba_llm = llm or ScriptedLLM(DBA_SCRIPT)

    resources = build_resources(commander_llm, context_max_tokens)
    targets = classify_targets(incident_text)
    incident_id, forks = make_investigation_forks(incident_text, resources, targets)
    await investigate(forks, dba_llm)
    ctx = merge_investigation(forks, incident_id, incident_text)

    runtime = decision_runtime(ctx)
    await runtime.arun()

    # Simulate the human: approve every gate (destructive-tool + any
    # low-confidence verification escalation) — same idiom `supervisor`'s
    # `run()` uses. A real app streams these and waits for an actual answer.
    while ctx.has_pending_question():
        question = ctx.latest_pending_question()
        assert question is not None
        ctx.resume(question.id, "yes")
        await runtime.arun()

    return ctx
