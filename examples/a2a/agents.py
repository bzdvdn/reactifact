"""Server-side agents for the A2A example — deterministic, no LLM.

The A2A server seeds a `UserMessage` (its default input), runs the agents to a
fixpoint, and reads the reply from the last artifact with a `text` field — so a
plain `Produce` is all it takes to be an A2A agent.

- `upper`     — uppercases the request (a "happy path" skill).
- `clarifier` — asks a question first (a remote task in `input-required`), then
  answers once the client resumes it. This is what exercises the HITL mapping.
"""

from __future__ import annotations

from reactifact import Agent, Consume, Produce, ProduceCall, create_agent
from reactifact.a2a.models import UserMessage
from reactifact.interrupt import PendingQuestion

from .models import Answer


class UpperCase(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        if call.trigger is None:
            return
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


class ClarifyThenAnswer(Produce[Answer]):
    """Asks `Question` on the first pass, answers once the client resumes."""

    artifact_type = Answer
    also_creates = (PendingQuestion,)

    async def produce(self, call: ProduceCall) -> None:
        if call.trigger is None:
            return
        data = call.trigger.data
        if isinstance(data, PendingQuestion):
            if data.answered:
                call.effects.create(Answer(text=f"got it: {data.resolution}"))
            return
        call.effects.ask("Which format should I use?", kind="clarify")


def upper_agent() -> Agent:
    """A one-step A2A skill: uppercase the request."""
    return create_agent(
        "upper", consumes=[Consume(UserMessage)], produces=[UpperCase()]
    )


def clarifier_agent() -> Agent:
    """A HITL A2A skill: ask, then answer after the client resumes."""
    return create_agent(
        "clarifier",
        consumes=[Consume(UserMessage), Consume(PendingQuestion)],
        produces=[ClarifyThenAnswer()],
    )


__all__ = ["ClarifyThenAnswer", "UpperCase", "clarifier_agent", "upper_agent"]
