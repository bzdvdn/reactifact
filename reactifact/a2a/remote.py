"""A2A remote agent as a first-class reactifact node.

`A2AAgentTool` lets an *LLM* call a remote agent. This is the other half: a
remote agent the **runtime schedules** — the analogue of LangGraph's
`A2ARemoteGraph`. A `Produce` consumes a typed input artifact, sends it to a
remote A2A agent with `message/send`, and creates a typed output artifact from
the reply; provenance, budget, guardrails and eval all apply as for a local
agent.

    from reactifact.a2a import remote_agent

    agent = remote_agent(
        "translator",
        "https://agent.example.com/a2a",
        consumes=[Consume(Question)],
        output_type=Answer,
    )

When the remote task comes back `input-required`, the produce raises a
`PendingQuestion` (`effects.ask`); a later `context.resume(...)` continues the
same remote task (the task id is carried in the question's `notes`) and lands
the final artifact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..agents import Agent, create_agent
from ..consume import Consume
from ..interrupt import PendingQuestion
from ..produce import Produce, ProduceCall
from .client import A2AClient
from .models import Message, Task

#: Renders the input artifact into the text sent to the remote agent.
MessageOf = Callable[[Any], str]
#: Builds the output artifact from the reply text and the original input data.
BuildOutput = Callable[[str, Any], Any]


def _default_message_of(data: Any) -> str:
    text = getattr(data, "text", None)
    if isinstance(text, str):
        return text
    dump = getattr(data, "model_dump_json", None)
    if callable(dump):
        return str(dump())
    return str(data)


def _reply_text(result: Task | Message) -> str:
    if isinstance(result, Message):
        return result.text
    if result.status.message is not None and result.status.message.text:
        return result.status.message.text
    return "\n".join(part.as_text() for a in result.artifacts for part in a.parts)


class A2ARemoteProduce(Produce[Any]):
    """A `Produce` that delegates its trigger to a remote A2A agent.

    `output_type` is the artifact created from the reply (default builder calls
    `output_type(text=reply)`; pass `build_output` for anything else).
    `message_of` renders the input artifact into the message text. `name` scopes
    the HITL question so several remote agents can share one `Runtime`.
    """

    def __init__(
        self,
        target: str | A2AClient,
        *,
        output_type: type[Any],
        name: str = "",
        message_of: MessageOf | None = None,
        build_output: BuildOutput | None = None,
        card_url: str | None = None,
        headers: dict[str, str] | None = None,
        client: Any | None = None,
    ) -> None:
        self._client = (
            target
            if isinstance(target, A2AClient)
            else A2AClient(target, card_url=card_url, headers=headers, client=client)
        )
        self._name = name
        self._message_of = message_of or _default_message_of
        self._build_output: BuildOutput = build_output or (
            lambda reply, _data: output_type(text=reply)
        )
        super().__init__(artifact_type=output_type, also_creates=(PendingQuestion,))

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        if trigger is None:
            return
        data = trigger.data
        resuming = isinstance(data, PendingQuestion)
        if resuming:
            if not data.answered:
                return
            notes = data.notes
            original = (
                call.context.get(notes["a2a_input_id"])
                if notes.get("a2a_input_id")
                else None
            )
            source = original.data if original is not None else data
            result = await self._client.send(
                data.resolution or "",
                task_id=notes.get("a2a_task_id"),
                context_id=notes.get("a2a_context_id"),
            )
        else:
            source = data
            result = await self._client.send(self._message_of(data))

        if isinstance(result, Task) and result.status.state == "input-required":
            question = (
                result.status.message.text
                if result.status.message is not None
                else "The remote agent needs more input."
            )
            input_id = data.notes.get("a2a_input_id", "") if resuming else trigger.id
            call.effects.ask(
                question,
                kind="clarify",
                notes={
                    "a2a_agent": self._name,
                    "a2a_task_id": result.id,
                    "a2a_context_id": result.contextId,
                    "a2a_input_id": input_id,
                },
            )
            return
        call.effects.create(self._build_output(_reply_text(result), source))


def remote_agent(
    name: str,
    target: str | A2AClient,
    *,
    consumes: Sequence[Consume],
    output_type: type[Any],
    message_of: MessageOf | None = None,
    build_output: BuildOutput | None = None,
    card_url: str | None = None,
    headers: dict[str, str] | None = None,
    client: Any | None = None,
) -> Agent:
    """Builds an `Agent` that runs a remote A2A agent as a scheduled node.

    `consumes` are the local artifact types that wake it; `output_type` is what
    it produces. A `PendingQuestion` resume is wired automatically (scoped by
    `name`), so a remote `input-required` surfaces as HITL in the local run.
    """
    produce = A2ARemoteProduce(
        target,
        output_type=output_type,
        name=name,
        message_of=message_of,
        build_output=build_output,
        card_url=card_url,
        headers=headers,
        client=client,
    )
    resume = Consume(
        PendingQuestion,
        condition=lambda artifact: (
            getattr(artifact.data, "answered", False)
            and artifact.data.notes.get("a2a_agent") == name
        ),
    )
    return create_agent(name, consumes=[*consumes, resume], produces=[produce])


__all__ = ["A2ARemoteProduce", "BuildOutput", "MessageOf", "remote_agent"]
