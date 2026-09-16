"""incident_commander demo: the DBA sub-agent, the `ask_dba` delegation
tool, and the Commander (decide + remediate, once investigation is done)."""

from __future__ import annotations

from reactifact import Agent, Consume, PendingQuestion, Produce
from reactifact.agent_tool import AgentAsTool, SubTask
from reactifact.llm_agent import HITLLMAgent
from reactifact.resources import RuntimeResources
from reactifact.verify import Verify

from .models import Answer, InvestigationComplete
from .tools import check_db_locks, restart_pod, rollback_deploy


def dba_agent_factory() -> Agent:
    """Builds the DBA specialist sub-agent — delegated to via `ask_dba`
    (`agent_tool.AgentAsTool`) on the database investigation fork
    (`produce.DBInvestigator`). A plain `HITLLMAgent`; it doesn't know it's
    being called as a tool."""

    class DBASpecialist(HITLLMAgent):
        name = "dba_specialist"
        system = (
            "You are a database specialist. Check for lock contention or slow "
            "queries and report briefly whether the database is a likely cause "
            "of the incident described."
        )
        tools = [check_db_locks]
        consumes = [Consume(SubTask)]

    return DBASpecialist()


def build_ask_dba(dba_resources: RuntimeResources) -> AgentAsTool:
    """`dba_resources` is the sub-agent's own `RuntimeResources` (its own
    LLM) — `AgentAsTool` can't inherit the parent's `Context.resources`,
    `Tool.execute` has no `context` at all."""
    return AgentAsTool(
        name="ask_dba",
        description="Delegate a database-focused question to the DBA specialist.",
        agent_factory=dba_agent_factory,
        resources=dba_resources,
    )


def build_commander() -> Agent:
    """Commander no longer investigates itself — the k8s/db forks already
    did that (`produce.py`) before merging back. Its job is narrower now:
    decide on a fix from the evidence already in context, apply it (behind
    the destructive-tool approval gate), and report."""

    class Commander(HITLLMAgent):
        name = "commander"
        system = (
            "You are an incident commander. The investigation is already done "
            "— evidence is available in context. Decide on a fix, apply it with "
            "a tool call, then give a final answer summarizing the root cause "
            "and the fix applied."
        )
        tools = [restart_pod, rollback_deploy]
        consumes = [Consume(InvestigationComplete)]
        max_steps = 6

    return Commander()


def build_verifier() -> Agent:
    """Scores the Commander's `Answer` with `eval.py`'s ground-truth-free
    metrics before the incident is considered resolved (`verify.Verify`)."""

    class VerifierAgent(Agent):
        name = "verifier"
        consumes = [Consume(Answer)]
        produces = [
            Verify(required_metrics=("provenance_grounded",)),
            # widens this agent's allowed Create types — Verify also
            # creates PendingQuestion (on_fail="ask") — see verify.py.
            Produce(PendingQuestion),
        ]

    return VerifierAgent()
