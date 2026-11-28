"""`recipes.approval` — typed/deterministic/LLM approval interpretation."""

from __future__ import annotations

import asyncio

from reactifact import FakeLLM, PendingQuestion, Pipeline
from reactifact.provenance import DERIVED_FROM
from reactifact.recipes import (
    ApprovalDecision,
    ApprovalFlow,
    ApprovalPolicy,
    ClarifyApproval,
    InterpretApproval,
    classify_approval,
)


def _question(
    *,
    resolution: str = "",
    data: dict | None = None,
    kind: str = "approval",
) -> PendingQuestion:
    return PendingQuestion(
        question="Deploy to production?",
        kind=kind,
        answered=True,
        resolution=resolution,
        resolution_data=data,
    )


def _intent(
    question: PendingQuestion,
    policy: ApprovalPolicy | None = None,
    llm=None,
):
    return asyncio.run(classify_approval(llm, question, policy=policy))


def test_approval_flow_builds_an_agent_with_an_explicit_wake_set():
    agent = ApprovalFlow().agent("approval")

    assert agent.name == "approval"
    types = {consume.artifact_type for consume in agent.consumes or ()}
    assert types == {PendingQuestion, ApprovalDecision}
    assert [step.name for step in agent.steps or ()] == [
        "InterpretApproval",
        "ClarifyApproval",
    ]


def test_typed_answer_never_uses_a_model():
    assert _intent(_question(data={"approved": True})).decision == "approve"
    assert _intent(_question(data={"approved": False})).decision == "reject"


def test_explicit_yes_no_and_conditional_are_deterministic():
    assert _intent(_question(resolution="yes")).decision == "approve"
    assert _intent(_question(resolution="да, запускай")).decision == "approve"
    assert _intent(_question(resolution="no")).decision == "reject"
    assert _intent(_question(resolution="нет, не надо")).decision == "reject"
    assert _intent(_question(resolution="if tests pass")).decision == "conditional"
    assert _intent(_question(resolution="если тесты зелёные")).decision == "conditional"


def test_uncertainty_and_substrings_are_not_approval():
    assert _intent(_question(resolution="maybe")).decision == "ambiguous"
    assert _intent(_question(resolution="yesterday")).decision == "ambiguous"
    assert _intent(_question(resolution="token")).decision == "ambiguous"


def test_without_a_model_an_unclear_answer_stays_ambiguous():
    intent = _intent(_question(resolution="please proceed"))

    assert intent.decision == "ambiguous"
    assert intent.confidence == 0.0


def test_custom_policy_vocabulary_avoids_the_model():
    policy = ApprovalPolicy(approve_phrases=("lgtm",), reject_phrases=("hold",))

    assert _intent(_question(resolution="lgtm"), policy).decision == "approve"
    assert _intent(_question(resolution="hold"), policy).decision == "reject"


def test_policy_prompt_and_hash_are_visible():
    policy = ApprovalPolicy(
        instructions="'LGTM' means approve.",
        examples=(("lgtm", "approve"),),
    )
    system, user = policy.prompt(_question(resolution="lgtm"))

    assert "approval" in system
    assert "'LGTM' means approve." in user
    assert '"lgtm" -> approve' in user
    assert policy.policy_hash != ApprovalPolicy().policy_hash


def test_interpret_step_records_the_policy_hash():
    policy = ApprovalPolicy(instructions="custom")
    result = Pipeline(steps=[InterpretApproval(policy=policy)]).run(
        _question(resolution="yes")
    )
    decision = result.context.latest(ApprovalDecision)

    assert decision is not None
    assert decision.data.decision == "approve"
    assert decision.data.policy_hash == policy.policy_hash


def test_llm_fallback_is_used_only_for_unclear_text():
    llm = FakeLLM(
        response=(
            '{"decision": "approve", "confidence": 0.94, "reason": "clear go-ahead"}'
        )
    )
    question = _question(resolution="please proceed")

    intent = asyncio.run(classify_approval(llm, question))

    assert intent.decision == "approve"
    assert intent.confidence == 0.94


def test_low_llm_confidence_is_never_approval():
    llm = FakeLLM(
        response='{"decision": "approve", "confidence": 0.3, "reason": "guess"}'
    )
    question = _question(resolution="please proceed")

    intent = asyncio.run(classify_approval(llm, question))

    assert intent.decision == "ambiguous"
    assert intent.confidence == 0.3


def test_interpret_step_creates_a_linked_decision():
    result = Pipeline(steps=[InterpretApproval()]).run(_question(resolution="yes"))
    decision = result.context.latest(ApprovalDecision)

    assert decision is not None
    assert decision.data.decision == "approve"
    assert decision.data.answer == "yes"
    assert result.context.related(decision.id, DERIVED_FROM)


def test_clarify_step_asks_a_typed_follow_up_once():
    ambiguous = ApprovalDecision(
        question_id="q1",
        decision="ambiguous",
        confidence=0.2,
    )
    result = Pipeline(steps=[ClarifyApproval()]).run(ambiguous)
    pending = result.context.pending_questions()

    assert len(pending) == 1
    assert pending[0].data.kind == "approval"
    assert (
        pending[0].data.resolution_schema["properties"]["approved"]["type"] == "boolean"
    )
    assert pending[0].data.notes["approval_of"] == "q1"


def test_approve_does_not_trigger_clarification():
    approved = ApprovalDecision(
        question_id="q1",
        decision="approve",
        confidence=0.95,
    )
    result = Pipeline(steps=[ClarifyApproval()]).run(approved)

    assert result.context.pending_questions() == []


def test_example_round_trip_asks_then_deploys():
    from examples.approval_gate import main

    result = asyncio.run(main())

    assert result["waiting"]["waiting"] is True
    assert result["answer"]["reply"] == "deployed 'checkout'"
    assert result["answer"]["waiting"] is False


def test_approval_flow_hides_the_internal_steps():
    from reactifact.recipes import ApprovalFlow

    flow = ApprovalFlow()
    assert [type(step).__name__ for step in flow] == [
        "InterpretApproval",
        "ClarifyApproval",
    ]

    without_clarify = ApprovalFlow(clarify=False)
    assert len(without_clarify) == 1
