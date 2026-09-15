import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    PendingQuestion,
    Produce,
    Runtime,
    RuntimeResources,
)
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
    class VerifierAgent(Agent):
        consumes = [Consume(Answer)]
        produces = [
            Verify(**verify_kwargs),
            Produce(PendingQuestion),
            Produce(VerificationFailed),
        ]

    return Runtime(ctx, agents=[VerifierAgent()])


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
