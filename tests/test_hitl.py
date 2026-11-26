import asyncio

from pydantic import BaseModel
from reactifact import Consume, Context, Patch, Runtime, SessionStore, StepGroup
from reactifact.checkpoints import FileKVBackend
from reactifact.interrupt import PendingQuestion


class Project(BaseModel):
    name: str
    budget: int


class RefundApproval(BaseModel):
    approved: bool
    note: str = ""


class Estimate(BaseModel):
    total: int


class ApprovalGate(StepGroup):
    """Requests budget approval and waits for a human answer."""

    consumes = [Consume(Project)]
    produces = []

    async def run(self, event, context):
        project = context.get(event.artifact_id)
        if project is None:
            return None
        return Patch().create(
            PendingQuestion(
                question=f"Одобрить бюджет {project.data.name} на {project.data.budget}?",
                kind="approval",
                notes={"project_budget": project.data.budget},
            )
        )


class Estimator(StepGroup):
    """Computes the estimate only after approval."""

    consumes = [
        Consume.by_field(PendingQuestion, "answered", True),
        Consume(Project),
    ]
    produces = []

    async def run(self, event, context):
        projects = context.list_artifacts(Project)
        if not projects:
            return None
        approved = context.list_artifacts(PendingQuestion)
        if not any(q.data.answered for q in approved):
            return None
        total = sum(p.data.budget for p in projects)
        return Patch().create(Estimate(total=total))


def make_runtime(session):
    return Runtime(
        session.context, groups=[ApprovalGate(), Estimator()], session=session
    )


async def make_hitl_runtime(tmp_path):
    store = SessionStore(FileKVBackend(str(tmp_path)))
    session = await store.open("hitl")
    return session, make_runtime(session), store


def test_hitl_wait_then_resume(tmp_path):
    asyncio.run(_test_hitl_wait_then_resume(tmp_path))


async def _test_hitl_wait_then_resume(tmp_path):
    session, runtime, store = await make_hitl_runtime(tmp_path)
    ctx = session.context

    ctx.create(Project(name="office", budget=50_000))
    await runtime.arun()

    assert ctx.has_pending_question()
    question = ctx.latest_pending_question()
    assert question.data.question == "Одобрить бюджет office на 50000?"
    assert question.data.kind == "approval"
    assert ctx.list_artifacts(Estimate) == []  # estimate not built yet

    # Restart: the pending question survives a session reload
    session2 = await store.open("hitl")
    assert session2.context.has_pending_question()

    # A human answer is a regular patch; work can continue with a new runtime
    restored = session2.context.latest_pending_question()
    session2.context.resume(restored.id, "approved")
    runtime2 = make_runtime(session2)
    await runtime2.arun()

    estimates = session2.context.list_artifacts(Estimate)
    assert len(estimates) == 1
    assert estimates[0].data.total == 50_000
    assert not session2.context.has_pending_question()


def test_resume_persists_answer(tmp_path):
    asyncio.run(_test_resume_persists_answer(tmp_path))


async def _test_resume_persists_answer(tmp_path):
    session, runtime, store = await make_hitl_runtime(tmp_path)
    ctx = session.context

    ctx.create(Project(name="kitchen", budget=20_000))
    await runtime.arun()
    question = ctx.latest_pending_question()
    ctx.resume(question.id, "reject")

    # auto-persist happens on the estimate commit; here the answer is working code.
    # But after arun (estimate), the answered state is already saved.
    await runtime.arun()
    await session.save()

    restored = await store.load_session("hitl")
    q = restored.list_artifacts(PendingQuestion)[0]
    assert q.data.answered is True
    assert q.data.resolution == "reject"


def test_resume_wrong_id_returns_none(tmp_path):
    asyncio.run(_test_resume_wrong_id_returns_none(tmp_path))


async def _test_resume_wrong_id_returns_none(tmp_path):
    session, runtime, store = await make_hitl_runtime(tmp_path)
    ctx = session.context
    ctx.create(Project(name="x", budget=1))
    await runtime.arun()
    assert ctx.resume("nonexistent", "yes") is None
    assert ctx.has_pending_question()


def test_multiple_questions_isolated(tmp_path):
    asyncio.run(_test_multiple_questions_isolated(tmp_path))


async def _test_multiple_questions_isolated(tmp_path):
    session, runtime, store = await make_hitl_runtime(tmp_path)
    ctx = session.context

    first = ctx.interrupt("Q1")
    second = ctx.interrupt("Q2")
    assert len(ctx.pending_questions()) == 2

    ctx.resume(first.id, "a1")
    remaining = ctx.pending_questions()
    assert [q.id for q in remaining] == [second.id]
    assert ctx.has_pending_question()  # Q2 is still pending

    ctx.resume(second.id, "a2")
    assert not ctx.has_pending_question()


def test_interrupt_stores_answer_schema():
    ctx = Context()
    question = ctx.interrupt(
        "Approve the refund?",
        kind="approve",
        schema=RefundApproval,
    )

    assert question.data.resolution_schema == RefundApproval.model_json_schema()


def test_resume_with_typed_model_and_mapping():
    ctx = Context()
    model_question = ctx.interrupt("Approve?", kind="approve", schema=RefundApproval)
    mapping_question = ctx.interrupt(
        "Approve?",
        kind="approve",
        schema={"type": "object", "properties": {"approved": {"type": "boolean"}}},
    )

    ctx.resume(model_question.id, RefundApproval(approved=True, note="ok"))
    ctx.resume(mapping_question.id, {"approved": False})

    model_answer = ctx.get(model_question.id)
    assert model_answer is not None
    assert model_answer.data.resolution_data == {"approved": True, "note": "ok"}
    assert model_answer.data.resolution == '{"approved": true, "note": "ok"}'
    assert model_answer.data.approved() is True
    assert model_answer.data.answer_text() == "yes"

    mapping_answer = ctx.get(mapping_question.id)
    assert mapping_answer is not None
    assert mapping_answer.data.resolution_data == {"approved": False}
    assert mapping_answer.data.approved() is False
    assert mapping_answer.data.answer_text() == "no"


def test_legacy_string_resume_still_works():
    ctx = Context()
    question = ctx.interrupt("Approve?", kind="approve")

    ctx.resume(question.id, "yes")

    updated = ctx.get(question.id)
    assert updated is not None
    assert updated.data.resolution == "yes"
    assert updated.data.resolution_data is None
    assert updated.data.approved() is True
    assert updated.data.answer_text() == "yes"


def test_typed_clarify_answer_uses_text_field():
    ctx = Context()
    question = ctx.interrupt(
        "Which environment?",
        kind="clarify",
        schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    ctx.resume(question.id, {"text": "prod"})

    updated = ctx.get(question.id)
    assert updated is not None
    assert updated.data.answer_text() == "prod"
    assert updated.data.approved() is False
