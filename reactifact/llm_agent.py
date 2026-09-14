"""LLM agent containers: thin, without logic.

- `LLMAgent` — ordinary: reactive/blocking? no — simple. Uses
  `ToolUse` (blocking loop without HITL).
- `HITLLMAgent` — can ask clarifying questions to a human through
  `ToolUseHITL` (reactive loop, `PendingQuestion`).

Both add boilerplate: `Consume.by_field(ToolAnswer, "agent", name)`,
`ToolUse(...)`, and the HITL variant also `Consume(Observation)`,
`Consume(PendingQuestion)` + produces Observation/PendingQuestion.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from .agents import Agent
from .artifacts import Artifact
from .consume import Consume
from .context import Context
from .events import Event
from .interrupt import PendingQuestion
from .produce import Produce
from .structured import SYSTEM_STRUCTURED, structured_llm
from .tool_use import DeferredToolGroup, Observation, ToolAnswer, ToolUse, ToolUseHITL
from .tools import Tool


def _require_consumes(
    cls_name: str, instance_name: str, user_consumes: list[Any]
) -> None:
    if not user_consumes:
        raise ValueError(
            f"{cls_name} {instance_name!r} has no consumes: the tool loop needs a "
            "trigger. Specify at least one Consume(<question artifact>), e.g. "
            "consumes=[Consume(Question)]."
        )


class StructuredGenerateAgent(Agent):
    """Base agent: LLM → pydantic schema → Artifact (reads/commits as provenance).

    Declare `schema`, override `build_prompt(inputs)`; optionally
    `fallback(inputs)` for a deterministic fallback variant (§67).
    """

    schema: type[BaseModel] | None = None
    system_prompt: str = SYSTEM_STRUCTURED
    attempts: int = 2
    temperature: float | None = None
    max_tokens: int | None = None

    def build_prompt(self, inputs: list[Artifact[Any]]) -> str:
        raise NotImplementedError

    def fallback(self, inputs: list[Artifact[Any]]) -> BaseModel | None:
        return None

    async def run(self, event: Event, context: Context) -> None:
        inputs = self.collect_inputs(context)
        if not inputs or self.schema is None:
            return None
        text = self.build_prompt(inputs)
        result = await structured_llm(
            context,
            schema=self.schema,
            system=self.system_prompt,
            user=text,
            attempts=self.attempts,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if result is None:
            result = self.fallback(inputs)
        if result is None:
            return None
        from .effects import current_effects

        effects = current_effects()
        if effects is None:  # running outside the runtime — nothing to commit to
            return None
        effects.create(result)
        return None


class LLMAgent(Agent):
    """Ordinary LLM agent with tools: blocking loop, no HITL.

    In a subclass set: `system`, `tools`, `consumes` (questions), `produces`
    (handling of the final answer).
    """

    system: str = ""
    tools: Sequence[Tool] | dict[str, Tool] = ()
    max_steps: int = 8
    temperature: float | None = None
    max_tokens: int | None = None
    deferred_tool_groups: Sequence[DeferredToolGroup] = ()

    def __init__(self, *, name: str | None = None, **kwargs: Any):
        llm_name = name or self.name or self.__class__.__name__.lower()
        user_consumes = list(self.consumes) if self.consumes else []
        _require_consumes(self.__class__.__name__, llm_name, user_consumes)
        self.consumes = [
            *user_consumes,
            Consume.by_field(ToolAnswer, "agent", llm_name),
        ]
        user_produces = list(self.produces) if self.produces else []
        self.produces = [
            ToolUse(
                name=llm_name,
                system=self.system,
                tools=self.tools,
                max_steps=self.max_steps,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                deferred_tool_groups=self.deferred_tool_groups,
            ),
            Produce(ToolAnswer),
            *user_produces,
        ]
        super().__init__(name=name, **kwargs)


class HITLLMAgent(Agent):
    """LLM agent with HITL: reactive loop that can ask a human.

    Same as `LLMAgent`, plus: the LLM can return `type:"ask"` — a `PendingQuestion`
    (kind="clarify") is created, the loop waits for the answer, and the answer is
    returned as `Observation(source="user")` for the loop to continue.
    """

    system: str = ""
    tools: Sequence[Tool] | dict[str, Tool] = ()
    max_steps: int = 8
    max_asks: int = 2
    resume_announce: Callable[[str], str] | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    def __init__(self, *, name: str | None = None, **kwargs: Any):
        llm_name = name or self.name or self.__class__.__name__.lower()
        user_consumes = list(self.consumes) if self.consumes else []
        _require_consumes(self.__class__.__name__, llm_name, user_consumes)
        self.consumes = [
            *user_consumes,
            Consume.by_field(ToolAnswer, "agent", llm_name),
            Consume.by_field(Observation, "agent", llm_name),
            Consume(
                PendingQuestion,
                condition=lambda a: a.data.notes.get("agent") == llm_name,
            ),
        ]
        user_produces = list(self.produces) if self.produces else []
        self.produces = [
            ToolUseHITL(
                name=llm_name,
                system=self.system,
                tools=self.tools,
                max_steps=self.max_steps,
                max_asks=self.max_asks,
                resume_announce=self.resume_announce,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            ),
            Produce(ToolAnswer),
            Produce(Observation),
            Produce(PendingQuestion),
            *user_produces,
        ]
        super().__init__(name=name, **kwargs)
