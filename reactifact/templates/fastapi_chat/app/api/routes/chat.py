"""SSE chat + history + clear, scoped to the authenticated principal."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from reactifact.chat import ChatAssistant
from reactifact.web import sse

from app.api.deps import get_assistant, get_current_user, scope_session
from app.models import User

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


async def _frames(
    assistant: ChatAssistant,
    *,
    message: str,
    client_id: str,
    storage_id: str,
    user: User,
) -> AsyncIterator[str]:
    async for event in assistant.stream(
        message,
        session_id=storage_id,
        request={"user": user},
    ):
        if event.kind == "session":
            yield sse("session", {"session_id": client_id})
        elif event.kind == "status":
            yield sse("status", {"message": event.message})
        else:
            yield sse("message", dict(event.payload) or {"reply": ""})


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    assistant: ChatAssistant = Depends(get_assistant),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    ref = scope_session(user, body.session_id)
    return StreamingResponse(
        _frames(
            assistant,
            message=body.message,
            client_id=ref.client_id,
            storage_id=ref.storage_id,
            user=user,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history")
async def chat_history(
    session_id: str = Query(default=""),
    assistant: ChatAssistant = Depends(get_assistant),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    ref = scope_session(user, session_id)
    return await assistant.history(ref.storage_id)


@router.delete("/session")
async def clear_session(
    session_id: str = Query(default=""),
    assistant: ChatAssistant = Depends(get_assistant),
    user: User = Depends(get_current_user),
) -> dict[str, bool]:
    ref = scope_session(user, session_id)
    await assistant.store.delete_session(ref.storage_id)
    return {"ok": True}


__all__ = ["router"]
