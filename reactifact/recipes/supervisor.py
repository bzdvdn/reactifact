"""recipes.supervisor — classify → route, and ask-before-finalize (§60, §67).

Ported from `examples/supervisor`, generalized into two independent,
composable pieces (that example's `Specialist` step — actually doing the
routed work — stays fully domain: there is nothing generic about "what a
route does", so it isn't a recipe here):

- `Router` — classify a request into one of a fixed set of routes,
  idempotently, with a deterministic fallback (§67) when the classifying
  call is unavailable or returns something outside `routes`.
- `ApprovalGate` — ask a human to sign off on a report before finalizing it
  (§60 HITL). Tracks the *whole* history of a thread's approval questions,
  not just the unanswered ones, so re-entry after a rejection doesn't spawn
  a duplicate question chain. Uses `kind="approve"` — the same vocabulary
  `tool_use.py`'s destructive-tool gate and `verify.py`'s `Verify` already
  use, so one control-plane UI built for "approve" requests handles all
  three, instead of each recipe inventing its own `kind` string.

Neither calls an LLM — `Router.classify` and `ApprovalGate.on_approve`/
`on_reject` are domain hooks. As in `recipes.plan_execute`, `task_type`
should give `thread_field`/`route_field` defaults (e.g. `thread: str = ""`,
`route: str = ""`) so `build_task()`/`on_approve()`/`on_reject()` don't need
to fill them in — the recipe stamps the real values on every create.

    class MyRouter(Router[Request, Task]):
        request_type = Request
        task_type = Task
        routes = ("budget", "timeline", "quality")

        async def classify(self, context, request) -> str | None: ...
        def fallback_route(self, context, request) -> str: ...

    class MyGate(ApprovalGate[SpecialistReport, FinalReply]):
        report_type = SpecialistReport
        final_type = FinalReply

        def approval_question(self, context, report) -> str: ...
        async def on_approve(self, context, report) -> FinalReply: ...
        async def on_reject(self, context, report, answer) -> FinalReply: ...

    class Flow(Agent):
        consumes = [Consume(Request), Consume(Task), Consume(SpecialistReport),
                    Consume(PendingQuestion)]
        produces = [MyRouter().produce(), Specialist(), MyGate().produce(),
                    Produce(PendingQuestion)]  # widens allowed Create types, see verify.py

Kept as an inert placeholder (not `also_creates=`, see `produce.py`) because
none of the three real produces above is uniquely "the one that creates
`PendingQuestion`" — it's created by whichever of `Router`/`ApprovalGate`'s
own HITL machinery calls `effects.ask(...)` at runtime. `also_creates` is the
better fit when a *single* produce's own body is the one writing the extra
type (`self.effects.create(Bar(...))` inside an `artifact_type = Foo`
produce) — declare it there directly instead of adding a placeholder here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..interrupt import PendingQuestion
from ..produce import Produce, ProduceCall

RequestT = TypeVar("RequestT", bound=BaseModel)
TaskT = TypeVar("TaskT", bound=BaseModel)
ReportT = TypeVar("ReportT", bound=BaseModel)
FinalT = TypeVar("FinalT", bound=BaseModel)

_YES = {"y", "yes", "да", "ok", "okay", "approve", "approved", "true"}


class Router(Generic[RequestT, TaskT], ABC):
    """Classify a request into a `Task` carrying one of `routes` (§67)."""

    request_type: type[RequestT]
    task_type: type[TaskT]
    routes: tuple[str, ...]
    thread_field: str = "thread"
    route_field: str = "route"

    @abstractmethod
    async def classify(
        self, context: Context, request: Artifact[RequestT]
    ) -> str | None:
        """The chosen route, or `None`/anything outside `routes` to fall
        back to `fallback_route` — never dead-ends without a route (§67)."""

    def fallback_route(self, context: Context, request: Artifact[RequestT]) -> str:
        """Deterministic fallback when `classify` gives up. Default: the
        first declared route — override for keyword matching, etc."""
        return self.routes[0]

    def build_task(
        self, context: Context, request: Artifact[RequestT], route: str
    ) -> TaskT:
        """Constructs the `Task`; override to fill in extra fields.
        `thread_field`/`route_field` are stamped afterwards regardless."""
        return self.task_type()

    def produce(self) -> Produce[Any]:
        return _RouteProduce(self)


class ApprovalGate(Generic[ReportT, FinalT], ABC):
    """Ask a human to approve `report` before finalizing it (§60)."""

    report_type: type[ReportT]
    final_type: type[FinalT]
    thread_field: str = "thread"

    @abstractmethod
    def approval_question(self, context: Context, report: Artifact[ReportT]) -> str: ...

    @abstractmethod
    async def on_approve(self, context: Context, report: Artifact[ReportT]) -> FinalT:
        """Builds the final artifact once a human approves `report`."""

    @abstractmethod
    async def on_reject(
        self, context: Context, report: Artifact[ReportT], answer: str
    ) -> FinalT:
        """Builds the final artifact when a human rejects `report` (`answer`
        is their raw resolution text, for logging/explaining the rejection)."""

    def is_yes(self, answer: str) -> bool:
        return answer.strip().lower() in _YES

    def produce(self) -> Produce[Any]:
        return _ApprovalProduce(self)

    def _final_id(self, thread_id: str) -> str:
        return f"final:{thread_id}"

    def _report_of(self, context: Context, thread_id: str) -> Artifact[ReportT] | None:
        for report in context.list_artifacts(self.report_type):
            if getattr(report.data, self.thread_field, None) == thread_id:
                return report
        return None

    def _thread_id_of(self, artifact: Artifact[Any]) -> str | None:
        if isinstance(artifact.data, self.report_type):
            value = getattr(artifact.data, self.thread_field, None)
            return value if isinstance(value, str) else None
        if isinstance(artifact.data, PendingQuestion):
            question = artifact.data
            if question.kind != "approve" or not question.answered:
                return None
            value = question.notes.get("thread")
            return value if isinstance(value, str) else None
        return None


class _RouteProduce(Produce[Any]):
    def __init__(self, owner: Router[Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.task_type)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        owner = self.owner
        request = call.trigger
        if request is None or not isinstance(request.data, owner.request_type):
            return None
        task_id = f"task:{request.id}"
        if context.get(task_id) is not None:
            return None  # already routed (§42)
        route = await owner.classify(context, request)
        if route not in owner.routes:
            route = owner.fallback_route(context, request)
        task_data = owner.build_task(context, request, route).model_copy(
            update={owner.thread_field: request.id, owner.route_field: route}
        )
        handle = self.effects.create(task_data, id=task_id)
        handle.link("for_request", request.id)
        return None


class _ApprovalProduce(Produce[Any]):
    def __init__(self, owner: ApprovalGate[Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.final_type)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        owner = self.owner
        artifact = call.trigger
        if artifact is None:
            return None
        thread_id = owner._thread_id_of(artifact)
        if thread_id is None:
            return None
        if context.get(owner._final_id(thread_id)) is not None:
            return None  # already finalized
        report = owner._report_of(context, thread_id)
        if report is None:
            return None

        # the whole history of this thread's approval questions, not just
        # the unanswered ones — a rejection must not spawn a second ask
        thread_questions = [
            q
            for q in context.list_artifacts(PendingQuestion)
            if q.data.kind == "approve" and q.data.notes.get("thread") == thread_id
        ]
        if not thread_questions:
            self.effects.ask(
                owner.approval_question(context, report),
                kind="approve",
                notes={"thread": thread_id},
            )
            return None
        if any(not q.data.answered for q in thread_questions):
            return None  # still waiting for the human

        answer = thread_questions[-1].data.resolution or ""
        final_data = (
            await owner.on_approve(context, report)
            if owner.is_yes(answer)
            else await owner.on_reject(context, report, answer)
        )
        if hasattr(final_data, owner.thread_field):
            final_data = final_data.model_copy(update={owner.thread_field: thread_id})
        handle = self.effects.create(final_data, id=owner._final_id(thread_id))
        handle.link("based_on", report)
        return None
