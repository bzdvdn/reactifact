"""supervisor — a router specialist + HITL approval (CrewAI/AutoGen-style).

A `Request` is routed to a specialist produce (structured LLM decision, or the
deterministic keyword fallback); the specialist produces a `SpecialistReport`;
a supervisor produce then asks the human for **approval** (`effects.ask`) and
records the answer with `effects.resume` — yes → the final reply, no → an
honest "please refine" reply.

    uv run python -m examples.supervisor.main
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    PendingQuestion,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.prompts import PromptTemplate
from reactifact.providers import LLMProvider
from reactifact.structured import structured_llm


def build_llm() -> LLMProvider | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured -> offline."""
    import os

    from reactifact.providers import openai_llm, openrouter_llm

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    return None


ROUTES = {"budget", "timeline", "quality"}


class Request(BaseModel):
    text: str


class RouteBody(BaseModel):
    route: str


class Task(BaseModel):
    thread: str
    route: str


class SpecialistReport(BaseModel):
    thread: str
    route: str
    text: str


class FinalReply(BaseModel):
    thread: str
    text: str


class _Text(BaseModel):
    text: str


_ROUTE = PromptTemplate(
    """You classify a request. Reply with a single route: {routes}."""
)
_SPECIALIST = PromptTemplate(
    """You are the '{route}' specialist. Answer the request briefly and
actionably, strictly in scope of your specialty."""
)


def _route_of(text: str) -> str:
    low = text.lower()
    for route in ROUTES:
        if route in low:
            return route
    return "quality"


class RouteTask(Produce[Task]):
    # Flow also consumes Task/SpecialistReport/PendingQuestion — reacts_to
    # keeps this produce to just the Request event it cares about, instead
    # of an `isinstance(request.data, Request)` guard in the body; `trigger`
    # gets the resolved Request directly, guaranteed non-None.
    artifact_type = Task
    reacts_to = (Request,)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        request = call.trigger
        assert request is not None
        if context.get(f"task:{request.id}") is not None:
            return None
        body = await structured_llm(
            context,
            schema=RouteBody,
            system=_ROUTE.render(routes=", ".join(sorted(ROUTES))),
            user=request.data.text,
        )
        route = (
            body.route
            if body is not None and body.route in ROUTES
            else _route_of(request.data.text)
        )
        task = self.effects.create(
            Task(thread=request.id, route=route), id=f"task:{request.id}"
        )
        task.link("for_request", request.id)
        return None


class Specialist(Produce[SpecialistReport]):
    artifact_type = SpecialistReport
    reacts_to = (Task,)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        task = call.trigger
        assert task is not None
        request = context.get(task.data.thread)
        user_text = request.data.text if request is not None else task.data.thread
        body = await structured_llm(
            context,
            schema=_Text,
            system=_SPECIALIST.render(route=task.data.route),
            user=user_text,
        )
        text = (
            body.text
            if body is not None
            else f"(offline {task.data.route}) «{user_text[:80]}»"
        )
        report = self.effects.create(
            SpecialistReport(thread=task.data.thread, route=task.data.route, text=text),
            id=f"report:{task.data.thread}",
        )
        report.link("from_task", task)
        return None


class Supervisor(Produce[FinalReply]):
    """HITL gate (§60): ask for approval, then resume and answer accordingly."""

    artifact_type = FinalReply

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        report = next((r for r in context.list_artifacts(SpecialistReport)), None)
        if report is None or context.list_artifacts(FinalReply):
            return None
        thread = report.data.thread

        # look at ALL the thread's approval questions (answered ones included),
        # not just `pending_questions()` (which are the unanswered ones)
        thread_questions = [
            q
            for q in context.list_artifacts(PendingQuestion)
            if q.data.kind == "approval" and q.data.notes.get("thread") == thread
        ]
        if not thread_questions:
            self.effects.ask(
                f"Approve the specialist's answer ({report.data.route})?",
                kind="approval",
                notes={"thread": thread},
            )
            return None
        open_question = next((q for q in thread_questions if not q.data.answered), None)
        if open_question is not None:
            return None  # still waiting for the human

        question = thread_questions[-1]
        answer = (question.data.resolution or "").strip().lower()
        self.effects.resume(question, answer)
        if answer.startswith("да") or answer.startswith("yes") or answer in {"y", "ok"}:
            text = report.data.text
        else:
            text = "Rejected: please refine the request and try again."
        reply = self.effects.create(
            FinalReply(thread=thread, text=text), id=f"reply:{thread}"
        )
        reply.link("based_on", report)
        return None


class Flow(Agent):
    name = "supervisor"
    consumes = [
        Consume(Request),
        Consume(Task),
        Consume(SpecialistReport),
        Consume(PendingQuestion),
    ]
    produces = [
        RouteTask(),
        Specialist(),
        Supervisor(),
        # Supervisor.produce() creates PendingQuestion via `effects.ask(...)`,
        # but its own `artifact_type` is FinalReply — this bare `Produce`
        # widens the agent-level allowed-types set (`Runtime._validate_patch_types`
        # unions every produces[i].artifact_type) so that Create is not
        # rejected. Not a no-op: removing it breaks HITL approval.
        Produce(PendingQuestion),
    ]


def run(
    *,
    text: str = "Optimize the lighting and socket budget.",
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Request(text=text))
    _arun(Runtime(ctx, agents=[Flow()]))  # → the approval question waits

    pending = [q for q in ctx.pending_questions() if q.data.kind == "approval"]
    if pending:
        # simulate the human answer: `Context.resume` is the one-line HITL
        # idiom (same as `effects.resume` inside a produce, §60).
        ctx.resume(pending[0].id, "yes")
        _arun(Runtime(ctx, agents=[Flow()]))
    return ctx


def _arun(runtime: Runtime) -> None:
    asyncio.run(runtime.arun())


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.supervisor.main")
    parser.add_argument("--text", default="Optimize the lighting and socket budget.")
    args = parser.parse_args()

    ctx = run(text=args.text, llm=build_llm())
    replies = ctx.list_artifacts(FinalReply)
    reports = ctx.list_artifacts(SpecialistReport)
    print("supervisor · route → specialist → HITL approval")
    for r in reports:
        print(f"  [{r.data.route}] {r.data.text}")
    for rep in replies:
        print(f"  reply: {rep.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
