"""incident_commander demo: investigation (per-branch), synthesis (context
budget), and the final grounded Answer."""

from __future__ import annotations

from reactifact import Agent, Consume, Produce, ProduceCall
from reactifact.agent_tool import AgentAsTool
from reactifact.tool_use import ToolAnswer
from reactifact.tools import Tool

from .models import (
    Answer,
    Evidence,
    InvestigationComplete,
    InvestigationTask,
    RootCauseHypothesis,
)
from .tools import check_pods, check_recent_deploys


class K8sInvestigator(Produce[Evidence]):
    """Investigates the k8s side, on its own fork — deterministic, no LLM:
    there's no decision to make here, just facts to gather."""

    artifact_type = Evidence

    async def produce(self, call: ProduceCall) -> None:
        task = call.trigger
        if task is None or not isinstance(task.data, InvestigationTask):
            return None
        if task.data.target != "k8s":
            return None
        pods = await check_pods.execute({"namespace": "checkout"})
        deploys = await check_recent_deploys.execute({})
        self.effects.create(
            Evidence(query_id=task.data.incident_id, text=pods.text, score=0.8)
        )
        self.effects.create(
            Evidence(query_id=task.data.incident_id, text=deploys.text, score=0.8)
        )
        return None


class DBInvestigator(Produce[Evidence]):
    """Investigates the database side, on its own fork — delegates to the
    DBA specialist sub-agent instead of guessing (`agent_tool.AgentAsTool`).
    Called directly, the same way `ToolUseHITL` would call any tool, just
    without an LLM deciding to — this branch's only job is "ask the DBA"."""

    artifact_type = Evidence

    def __init__(self, ask_dba: Tool):
        self.ask_dba = ask_dba
        super().__init__()

    async def produce(self, call: ProduceCall) -> None:
        task = call.trigger
        if task is None or not isinstance(task.data, InvestigationTask):
            return None
        if task.data.target != "db":
            return None
        result = await self.ask_dba.execute(
            {
                "query": "Any DB lock contention or timeouts correlating with the checkout incident?"
            }
        )
        text = result.text or f"(dba specialist error) {result.error}"
        self.effects.create(
            Evidence(query_id=task.data.incident_id, text=text, score=0.8)
        )
        return None


class SynthesizeRootCause(Produce[RootCauseHypothesis]):
    """Rolling synthesis over the Evidence gathered so far.

    The point of this produce, for the demo, is what it *doesn't* do: no
    manual truncation logic here at all. `inputs` is whatever this agent's
    `consumes` matched, already ranked/bounded by
    `context.resources.context_builder` (`context_builder.py`) before this
    produce ever sees it — set `TokenBudgetContextBuilder(max_tokens=...)`
    on the `Runtime`'s resources and this produce is automatically
    budget-aware, with zero code changes. Triggered by `InvestigationComplete`
    (`Context.merge()` — unlike `merge_from()` — does not emit events for
    artifacts merged in from the other side, `branching.py`, so there is no
    event to react to for the *other* fork's `Evidence` once merged; the
    explicit marker is what actually wakes this up). Also consumes `Evidence`
    directly so it would still react if this agent were ever registered on a
    fork's own investigation `Runtime` too — it isn't, in this demo.
    """

    artifact_type = RootCauseHypothesis

    async def produce(self, call: ProduceCall) -> None:
        evidence = [i for i in call.inputs if isinstance(i.data, Evidence)]
        if not evidence:
            return None
        query_id = evidence[0].data.query_id
        text = " | ".join(e.data.text for e in evidence)
        self.effects.create(
            RootCauseHypothesis(
                query_id=query_id, text=text, evidence_seen=len(evidence)
            ),
            id=f"hypothesis:{query_id}",
        )
        return None


class BuildAnswer(Produce[Answer]):
    """`ToolAnswer` → `Answer`, linked `supported_by` → every `Evidence`
    gathered during the investigation (§34, from *both* forks once merged)
    — this is what makes `Verify`'s `provenance_grounded` metric non-trivial."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        tool_answer = call.trigger
        if tool_answer is None or not isinstance(tool_answer.data, ToolAnswer):
            return None
        # Commander's `qid` (`tool_answer.data.query_id`) is the id of
        # whatever triggered it — `InvestigationComplete`, not the incident
        # itself; resolve back to the shared `incident_id` Evidence is
        # actually tagged with (§ InvestigationComplete docstring).
        source = context.get(tool_answer.data.query_id)
        incident_id = (
            source.data.incident_id
            if source is not None and isinstance(source.data, InvestigationComplete)
            else tool_answer.data.query_id
        )
        evidence = [
            e
            for e in context.list_artifacts(Evidence)
            if e.data.query_id == incident_id
        ]
        answer = self.effects.create(
            Answer(query_id=incident_id, text=tool_answer.data.text)
        )
        for e in evidence:
            answer.link("supported_by", e)
        return None


class K8sInvestigatorAgent(Agent):
    name = "k8s_investigator"
    consumes = [Consume(InvestigationTask)]
    produces = [K8sInvestigator()]


def build_db_investigator_agent(ask_dba: AgentAsTool) -> Agent:
    class DBInvestigatorAgent(Agent):
        name = "db_investigator"
        consumes = [Consume(InvestigationTask)]
        produces = [DBInvestigator(ask_dba)]

    return DBInvestigatorAgent()


class SynthesizerAgent(Agent):
    name = "root_cause_synthesizer"
    consumes = [Consume(Evidence), Consume(InvestigationComplete)]
    produces = [SynthesizeRootCause()]


class AnswerAgent(Agent):
    name = "answer_builder"
    consumes = [Consume(ToolAnswer)]
    produces = [BuildAnswer()]
