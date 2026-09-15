import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, PendingQuestion, Produce, Runtime
from reactifact.recipes import ApprovalGate, Router

ROUTES = ("budget", "timeline", "quality")


class Request(BaseModel):
    text: str


class Task(BaseModel):
    thread: str = ""
    route: str = ""


class SpecialistReport(BaseModel):
    thread: str = ""
    route: str = ""
    text: str = ""


class FinalReply(BaseModel):
    thread: str = ""
    text: str = ""


class MyRouter(Router[Request, Task]):
    request_type = Request
    task_type = Task
    routes = ROUTES

    async def classify(self, context, request):
        low = request.data.text.lower()
        for route in self.routes:
            if route in low:
                return route
        return None  # forces fallback_route

    def fallback_route(self, context, request):
        return "quality"


class Specialist(Produce[SpecialistReport]):
    artifact_type = SpecialistReport

    async def produce(self, context, inputs, event=None):
        task = context.get(event.artifact_id) if event is not None else None
        if task is None or not isinstance(task.data, Task):
            return None
        report = self.effects.create(
            SpecialistReport(
                thread=task.data.thread,
                route=task.data.route,
                text=f"({task.data.route}) done",
            ),
            id=f"report:{task.data.thread}",
        )
        report.link("from_task", task)
        return None


class MyGate(ApprovalGate[SpecialistReport, FinalReply]):
    report_type = SpecialistReport
    final_type = FinalReply

    def approval_question(self, context, report):
        return f"Approve the {report.data.route} answer?"

    async def on_approve(self, context, report):
        return FinalReply(text=report.data.text)

    async def on_reject(self, context, report, answer):
        return FinalReply(text="Rejected: please refine the request and try again.")


def make_runtime(ctx: Context) -> Runtime:
    class Flow(Agent):
        consumes = [
            Consume(Request),
            Consume(Task),
            Consume(SpecialistReport),
            Consume(PendingQuestion),
        ]
        produces = [
            MyRouter().produce(),
            Specialist(),
            MyGate().produce(),
            Produce(PendingQuestion),
        ]

    return Runtime(ctx, agents=[Flow()])


def test_routes_via_llm_hook_then_waits_for_approval():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Request(text="Optimize the budget for socket."))
    asyncio.run(runtime.arun())

    tasks = ctx.list_artifacts(Task)
    assert len(tasks) == 1
    assert tasks[0].data.route == "budget"

    reports = ctx.list_artifacts(SpecialistReport)
    assert len(reports) == 1

    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "approve"
    assert not ctx.list_artifacts(FinalReply)


def test_falls_back_when_classify_returns_none():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Request(text="something unrelated entirely"))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Task)[0].data.route == "quality"


def test_approval_yes_produces_final_reply_with_report_text():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Request(text="check the timeline please"))
    asyncio.run(runtime.arun())

    question = ctx.pending_questions()[0]
    ctx.resume(question.id, "yes")
    asyncio.run(runtime.arun())

    replies = ctx.list_artifacts(FinalReply)
    assert len(replies) == 1
    assert replies[0].data.text == "(timeline) done"
    report = ctx.list_artifacts(SpecialistReport)[0]
    assert {a.id for a in ctx.related(replies[0].id, relation="based_on")} == {
        report.id
    }


def test_approval_no_produces_rejection_without_asking_twice():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Request(text="check the quality please"))
    asyncio.run(runtime.arun())

    question = ctx.pending_questions()[0]
    ctx.resume(question.id, "no")
    asyncio.run(runtime.arun())

    replies = ctx.list_artifacts(FinalReply)
    assert len(replies) == 1
    assert replies[0].data.text.startswith("Rejected")
    # exactly one approval question ever got created for this thread
    assert len(ctx.list_artifacts(PendingQuestion)) == 1
