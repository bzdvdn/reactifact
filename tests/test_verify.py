import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    PendingQuestion,
    Runtime,
    RuntimeResources,
)
from reactifact.eval import answer_present
from reactifact.verify import (
    DEFAULT_THRESHOLD,
    VerificationFailed,
    VerificationResult,
    Verify,
)


class Answer(BaseModel):
    text: str = ""


class Evidence(BaseModel):
    text: str = ""
    score: float = 0.0


def make_runtime(ctx: Context, **verify_kwargs) -> Runtime:
    class VerifierGroup(Agent):
        consumes = [Consume(Answer)]
        produces = [Verify(**verify_kwargs)]

    return Runtime(ctx, agents=[VerifierGroup()])


def test_grounded_well_evidenced_answer_passes():
    ctx = Context()
    runtime = make_runtime(ctx)

    evidence = ctx.create(Evidence(text="doc says so", score=0.9))
    answer = ctx.create(Answer(text="yes"))
    ctx.link(answer.id, "supported_by", evidence.id)
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.passed is True
    assert result.data.overall == 1.0
    assert not ctx.list_artifacts(PendingQuestion)


def test_ungrounded_answer_fails_default_threshold_and_asks():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Answer(text="unsupported claim"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    # answer_present=1, provenance_grounded=0, evidence_quality=0 (no evidence),
    # claim_verification=1 (no claims) -> overall 0.5, below DEFAULT_THRESHOLD
    assert result.data.overall == 0.5
    assert result.data.threshold == DEFAULT_THRESHOLD
    assert result.data.passed is False

    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "approve"


def test_framework_threshold_from_runtime_resources_lowers_the_bar():
    ctx = Context(resources=RuntimeResources(verification_threshold=0.3))
    runtime = make_runtime(ctx)

    ctx.create(Answer(text="unsupported claim"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.overall == 0.5
    assert result.data.threshold == 0.3
    assert result.data.passed is True
    assert not ctx.list_artifacts(PendingQuestion)


def test_instance_threshold_overrides_framework_default():
    # framework says 0.3 is fine, but this particular Verify insists on 0.9
    ctx = Context(resources=RuntimeResources(verification_threshold=0.3))
    runtime = make_runtime(ctx, threshold=0.9)

    ctx.create(Answer(text="unsupported claim"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.threshold == 0.9
    assert result.data.passed is False


def test_required_metrics_fail_even_with_a_passing_average():
    ctx = Context()
    runtime = make_runtime(
        ctx, threshold=0.4, required_metrics=("provenance_grounded",)
    )

    # overall 0.5 clears threshold=0.4, but provenance_grounded=0 is required
    ctx.create(Answer(text="unsupported claim"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.overall == 0.5
    assert result.data.passed is False
    assert result.data.required_failed == ["provenance_grounded"]


def test_on_fail_retry_creates_marker_instead_of_asking():
    ctx = Context()
    runtime = make_runtime(ctx, on_fail="retry")

    ctx.create(Answer(text="unsupported claim"))
    asyncio.run(runtime.arun())

    assert not ctx.list_artifacts(PendingQuestion)
    failures = ctx.list_artifacts(VerificationFailed)
    assert len(failures) == 1
    assert failures[0].data.overall == 0.5


def test_verify_validates_configuration() -> None:
    with pytest.raises(ValueError, match="threshold"):
        Verify(threshold=1.5)
    with pytest.raises(ValueError, match="on_fail"):
        Verify(on_fail="oops")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="required metrics"):
        Verify(required_metrics=("missing",))
    with pytest.raises(ValueError, match="verification_threshold"):
        ctx = Context(resources=RuntimeResources(verification_threshold=2.0))
        runtime = make_runtime(ctx)
        ctx.create(Answer(text="unsupported"))
        asyncio.run(runtime.arun())


def test_verify_copies_metrics_and_accepts_a_question() -> None:
    metrics = {"answer_present": answer_present}
    step = Verify(metrics=metrics, question="Accept? {reason}")
    metrics.clear()
    assert step.metrics == {"answer_present": answer_present}

    context = Context()
    runtime = make_runtime(context, question="Accept? {reason}")
    context.create(Answer(text="unsupported"))
    asyncio.run(runtime.arun())

    questions = context.list_artifacts(PendingQuestion)
    assert questions[0].data.question.startswith("Accept? ")
    assert "threshold=" in questions[0].data.question


def test_verify_records_skipped_required_metrics() -> None:
    def skipped(context, expected=None):
        return None

    ctx = Context()
    runtime = make_runtime(
        ctx,
        metrics={"answer_present": answer_present, "missing": skipped},
        required_metrics=("missing",),
        threshold=0.0,
    )
    ctx.create(Answer(text="unsupported"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.skipped == ["missing"]
    assert result.data.required_failed == ["missing"]


def test_verify_links_result_to_the_answer() -> None:
    ctx = Context()
    runtime = make_runtime(ctx)
    answer = ctx.create(Answer(text="unsupported"))
    asyncio.run(runtime.arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.answer_version == answer.version
    assert [item.id for item in ctx.related(result.id, relation="verifies")] == [
        answer.id
    ]


def test_verify_declares_all_outputs_and_accepts_a_model_class():
    assert Verify.produces == (
        VerificationResult,
        VerificationFailed,
        PendingQuestion,
    )

    ctx = Context()
    answer = ctx.create(Answer(text="unsupported"))
    asyncio.run(Runtime(ctx, steps=[Verify(Answer, threshold=0.0)]).arun())

    result = ctx.list_artifacts(VerificationResult)[-1]
    assert result.data.answer_id == answer.id
    assert result.data.passed is True


def test_verify_rejects_a_non_model_answer_type():
    with pytest.raises(TypeError, match="BaseModel"):
        Verify(str)  # type: ignore[arg-type]
