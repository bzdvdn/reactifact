"""The support_copilot produces: search → resolve → answer, or escalate.

A grounded reply is the matched document's own text (`supported_by` provenance);
when nothing matches, the agent asks a human instead of guessing.
"""

from __future__ import annotations

from reactifact import PendingQuestion, ProduceCall, produce
from reactifact.recipes import fan_out_sources, find
from reactifact.sources import SourceRef

from examples.support_copilot.models import Doc, Question, Reply, SearchDone


@produce(SourceRef, also_creates=(SearchDone,))
async def search(call: ProduceCall) -> None:
    question = find(call.inputs, Question)
    if question is None:
        return None
    refs = await fan_out_sources(
        call.context, question.data.text, owner_id=question.id, limit=3
    )
    call.effects.create(
        SearchDone(owner_id=question.id, count=len(refs)),
        id=f"searched:{question.id}",
    )
    return None


@produce(Doc)
async def resolve(call: ProduceCall) -> None:
    ref = call.trigger
    if ref is None or not isinstance(ref.data, SourceRef):
        return None
    source = call.context.resources.get_source(ref.data.source_id)
    if source is None:
        return None
    try:
        content = await source.resolve(ref.data)
    except Exception:  # an unreadable source is a None, not a crash
        return None
    call.effects.create(
        Doc(text=str(content), locator=ref.data.locator),
        id=f"doc:{ref.data.locator}",
    ).link("materialized_from", ref)
    return None


def _first_body_line(text: str) -> str:
    return next(
        (
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ),
        "See the documentation.",
    )


@produce(Reply)
async def answer(call: ProduceCall) -> None:
    """Grounded reply: the matched document's own text, with a citation."""
    doc = call.trigger
    if doc is None or not isinstance(doc.data, Doc):
        return None
    question = call.context.latest(Question)
    if question is None:
        return None
    handle = call.effects.create_once_from(
        question,
        Reply(text=_first_body_line(doc.data.text), citations=[doc.data.locator]),
    )
    if handle is None:
        return None
    handle.link("supported_by", doc)
    return None


@produce(PendingQuestion)
async def escalate(call: ProduceCall) -> None:
    """No document matched → ask a human, don't guess (§59, §60)."""
    done = call.trigger
    if done is None or not isinstance(done.data, SearchDone):
        return None
    if done.data.count > 0:
        return None
    call.effects.ask(
        "No help article matched this question. How should I answer?",
        kind="escalate",
        notes={"owner_id": done.data.owner_id},
        id=f"escalate:{done.data.owner_id}",
    )
    return None


@produce(Reply)
async def finalize(call: ProduceCall) -> None:
    """The human's answer to an escalation becomes the reply."""
    pending = call.trigger
    if pending is None or not isinstance(pending.data, PendingQuestion):
        return None
    if not pending.data.answered:
        return None
    question = call.context.latest(Question)
    if question is None or pending.id != f"escalate:{question.id}":
        return None
    handle = call.effects.create_once_from(
        question,
        Reply(text=pending.data.resolution or "", escalated=True),
    )
    if handle is None:
        return None
    handle.link("supported_by", pending)
    return None


__all__ = ["answer", "escalate", "finalize", "resolve", "search"]
