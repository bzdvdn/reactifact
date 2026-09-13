"""medic-lab web API — SSE chat/answer/runs endpoints (APIRouter).

Kept decoupled from the app factory: the router receives a `resources_factory`
(built in `main.py`) and the shared `store` / `trace_store`, so the API layer
does not know about .env or fixture paths.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from reactifact import Budget, Runtime, SessionStore
from reactifact.interrupt import PendingQuestion
from reactifact.tracing import Tracer

from ..agents import medic_lab_scheduler
from ..models import Question, ResearchReport

#: Runs per request before the runtime gives up (budget, §58).
MAX_RUNS = 400


class ChatRequest(BaseModel):
    message: str
    session_id: str


class AnswerRequest(BaseModel):
    session_id: str
    reply: str


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _report_payload(report: ResearchReport) -> dict[str, Any]:
    lines = [f"{r.statement}… [score {r.score}, {r.verdict}]" for r in report.ranking]
    return {
        "reply": f"{report.answer}\nUncertainty: {report.uncertainty}\n"
        + "\n".join(f"• {line}" for line in lines),
        "waiting": False,
        "steer": False,
        "sources": [],
    }


def _steer_payload(question: PendingQuestion) -> dict[str, Any]:
    return {"reply": question.question, "waiting": True, "steer": True, "sources": []}


async def _run_and_stream(
    ctx: Any, runtime: Runtime, msg_id: str
) -> AsyncIterator[str]:
    """Runs until a steering question or the final report; yields SSE statuses."""
    guard = 0
    while guard < 60:
        async for event in runtime.astream():
            if event.kind == "status":
                yield _sse("status", {"message": event.message})
        reports = [
            r
            for r in ctx.list_artifacts(ResearchReport)
            if r.data.question_id == msg_id
        ]
        if reports:
            yield _sse("message", _report_payload(reports[0].data))
            return
        pending = ctx.latest_pending_question()
        if pending is not None:
            yield _sse("message", _steer_payload(pending.data))
            return
        guard += 1


def create_router(
    *,
    store: SessionStore,
    agents: list[Any],
    resources_factory: Callable[[], Any],
    trace_store: Any = None,
) -> APIRouter:
    """Builds the medic-lab API router.

    Args:
        store: session store shared with the app factory.
        agents: the runtime agent set (see `medic_lab_agents`).
        resources_factory: returns a `RuntimeResources` (llm + sources) per call.
        trace_store: optional TraceStore — run traces land here (§54).
    """

    async def _open_runtime(session_id: str) -> tuple[Any, Runtime]:
        session = await store.open(session_id, resources=resources_factory())
        runtime = Runtime(
            session.context,
            agents=agents,
            session=session,
            budget=Budget(max_runs=MAX_RUNS),
            max_concurrency=6,
            tracer=Tracer(store=trace_store) if trace_store is not None else None,
            scheduler=medic_lab_scheduler(),
        )
        return session, runtime

    router = APIRouter()

    @router.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @router.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest) -> StreamingResponse:
        session, runtime = await _open_runtime(req.session_id)
        msg = session.context.create(
            Question(text=req.message, session_id=req.session_id)
        )

        async def stream() -> AsyncIterator[str]:
            yield _sse("session", {"session_id": req.session_id})
            async for frame in _run_and_stream(session.context, runtime, msg.id):
                yield frame
            await session.save()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/chat/answer")
    async def chat_answer(req: AnswerRequest) -> StreamingResponse:
        session, runtime = await _open_runtime(req.session_id)
        pending = session.context.latest_pending_question()
        active_question: str | None = None

        async def _latest_question() -> str | None:
            questions = session.context.list_artifacts(Question)
            return max(questions, key=lambda q: q.created_at).id if questions else None

        if pending is not None:
            session.context.resume(pending.id, req.reply)
            active_question = (
                pending.data.notes.get("question_id") or await _latest_question()
            )
        else:
            # Recovery: if the steer was already consumed (or the session is
            # stale), re-run the latest question — the lab will resurface a
            # steering question or assemble the report, instead of dead-ending.
            active_question = await _latest_question()

        async def stream() -> AsyncIterator[str]:
            if active_question is None:
                yield _sse(
                    "message",
                    {
                        "reply": "Nothing to continue — ask a new question.",
                        "waiting": False,
                        "steer": False,
                        "sources": [],
                    },
                )
                return
            async for frame in _run_and_stream(
                session.context, runtime, active_question
            ):
                yield frame
            await session.save()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/runs/{session_id}")
    async def runs(session_id: str) -> JSONResponse:
        session = await store.open(session_id)
        return JSONResponse(
            {
                "questions": [
                    m.data.text for m in session.context.list_artifacts(Question)
                ],
                "reports": [
                    a.data.answer
                    for a in session.context.list_artifacts(ResearchReport)
                ],
            }
        )

    @router.delete("/api/runs/{session_id}")
    async def run_delete(session_id: str) -> dict[str, str]:
        await store.delete_session(session_id)
        return {"ok": "true"}

    return router
