"""Artifact models the `quick` facade works with.

Generic on purpose: `Question`/`Doc`/`Answer` are enough to stand up the four
first tasks (one-shot answer, RAG, tools, chat) without declaring domain models
first. Swap them out the moment your domain has better names — the produce
helpers in this package are small and easy to fork.
"""

from __future__ import annotations

from pydantic import BaseModel


class Question(BaseModel):
    """The default input artifact for `quick` agents: one question/instruction.

    `session_id` is unused by `agent`/`rag`/`tools_agent` (it stays empty) but
    is what lets the same model be the default `user_message=` of
    `chat_agent(...)` — `ChatAssistant` stamps the current session onto the
    artifact it creates each turn.
    """

    text: str
    session_id: str = ""


class Doc(BaseModel):
    """A materialized source document (RAG).

    `locator` is the source's own address (file path, URL, ref locator) — what
    `Answer.sources` cites and what the `materialized_from` provenance edge
    points at.
    """

    text: str
    locator: str = ""
    title: str = ""


class Answer(BaseModel):
    """A RAG answer: the text plus the locators of the sources it used."""

    text: str
    sources: list[str] = []


__all__ = ["Answer", "Doc", "Question"]
