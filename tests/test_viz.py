"""viz rendering is deterministic and structurally sound."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Produce, ProduceCall, RuntimeResources
from reactifact.recipes import StatusMachine
from reactifact.tracing.models import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
)
from reactifact.viz import (
    blueprint,
    context_to_mermaid,
    trace_provenance_to_mermaid,
    trace_to_mermaid,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Turn(BaseModel):
    status: str = "new"


class Echo(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call: ProduceCall):
        return Patch().create(Answer(text="echo"))


class AdvanceTurn(StatusMachine[Turn]):
    artifact_type = Turn
    terminal = frozenset()

    def next_status(self, context, key):
        return "done"


class DiagramAgent(Agent):
    name = "diagram"
    consumes = [Consume(Question)]
    produces = [Echo(), AdvanceTurn()]


def test_blueprint_renders_agents_consumes_and_produces():
    diagram = blueprint([DiagramAgent()])
    assert diagram.startswith("flowchart LR")
    assert "diagram" in diagram
    assert "Consume" in diagram
    assert diagram.count("Question") >= 1
    assert diagram.count("Answer") >= 1
    assert diagram.count("Turn") >= 1
    assert "lifecycle" in diagram  # StatusMachine-style edge, not "creates"


def test_context_to_mermaid_groups_artifacts_and_relations():
    ctx = Context(resources=RuntimeResources())
    q = ctx.create(Question(text="what is the answer?"))
    a = ctx.create(Answer(text="42"))
    ctx.link(a.id, "supported_by", q.id)
    diagram = context_to_mermaid(ctx)
    assert diagram.startswith("flowchart TD")
    assert 'subgraph T0["Question"]' in diagram
    assert 'subgraph T1["Answer"]' in diagram
    assert '-->|"supported_by"|' in diagram
    assert "Question" in diagram and "Answer" in diagram


def test_context_to_mermaid_limit_restricts_relations():
    ctx = Context(resources=RuntimeResources())
    q = ctx.create(Question(text="q"))
    a = ctx.create(Answer(text="a"))
    ctx.link(a.id, "supported_by", q.id)
    diagram = context_to_mermaid(ctx, limit=1)
    assert '-->|"supported_by"|' not in diagram  # the second artifact is hidden


def test_trace_to_mermaid_sequence_with_llm():
    trace = RunTrace(
        id="run-1",
        outcome="completed",
        duration_ms=123.0,
        spans=[
            AgentSpan(
                agent="searcher",
                event_type="SearchDone",
                reads=[],
                writes=[_ref("ref:1:q")],
                llm_calls=[],
                latency_ms=10.0,
            ),
            AgentSpan(
                agent="verifier",
                event_type="Evidence",
                reads=[_ref("ev:1")],
                writes=[_ref("claim:1")],
                llm_calls=[
                    LLMCall(
                        model="deepseek-chat",
                        prompt_tokens=12,
                        completion_tokens=31,
                        latency_ms=80.0,
                    )
                ],
                latency_ms=90.0,
            ),
        ],
    )
    diagram = trace_to_mermaid(trace)
    assert diagram.startswith("sequenceDiagram")
    assert "participant RT as Runtime" in diagram
    assert '"searcher"' in diagram
    assert "SearchDone" in diagram
    assert "LLM (recording)" in diagram
    assert "12 in → 31 out" in diagram
    assert "outcome=completed" in diagram


def _ref(artifact_id: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        op_type="create",
        data_type="dummy.Dummy",
        data="{}",
    )


def test_trace_provenance_renders_nodes_and_edges():
    trace = RunTrace(
        id="run-1",
        outcome="completed",
        spans=[
            AgentSpan(
                agent="answerer",
                event_type="Answer",
                writes=[
                    ArtifactRef(artifact_id="answer:q1", data_type="example.Answer"),
                    ArtifactRef(artifact_id="claim:1", data_type="example.Claim"),
                    ArtifactRef(artifact_id="ev:1", data_type="example.Evidence"),
                ],
                relations=[
                    RelationRef(
                        source_id="answer:q1",
                        relation="supported_by",
                        target_id="claim:1",
                        source_type="Answer",
                        target_type="Claim",
                    ),
                    RelationRef(
                        source_id="claim:1",
                        relation="derived_from",
                        target_id="ev:1",
                        source_type="Claim",
                        target_type="Evidence",
                    ),
                ],
            )
        ],
    )
    diagram = trace_provenance_to_mermaid(trace)
    assert diagram.startswith("flowchart TD")
    assert "Answer:answer:q1" in diagram
    assert "Evidence:ev:1" in diagram
    assert '-->|"supported_by"|' in diagram
    assert '-->|"derived_from"|' in diagram


def test_trace_provenance_empty_is_graceful():
    trace = RunTrace(id="run-empty", outcome="completed")
    diagram = trace_provenance_to_mermaid(trace)
    assert diagram.startswith("flowchart TD")
    assert "no provenance recorded" in diagram
