"""LLM agent containers: thin, without logic.

- `LLMAgent` — blocking tool loop (`ToolUse`).
- `HITLLMAgent` — reactive tool loop that can ask a human (`ToolUseHITL`).
- `StructuredGenerateAgent` — LLM → pydantic schema → artifact.

The tool agents share their constructor in `_ToolAgentBase`; it wires the
tool-loop step and the `ToolAnswer` subscription. HITL adds the
`Observation`/`PendingQuestion` subscriptions it needs to resume.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from .agent import Agent, Step
from .artifacts import Artifact
from .consume import Consume
from .context import Context
from .events import Event
from .interrupt import PendingQuestion
from .structured import SYSTEM_STRUCTURED, structured_llm
from .tools import Tool
from .tools.use import DeferredToolGroup, Observation, ToolAnswer, ToolUse, ToolUseHITL


def _require_consumes(
    cls_name: str, instance_name: str, user_consumes: Sequence[Any]
) -> None:
    if not user_consumes:
        raise ValueError(
            f"{cls_name} {instance_name!r} has no consumes: the tool loop needs a "
            "trigger. Specify at least one Consume(<question artifact>), e.g. "
            "consumes=[Consume(Question)]."
        )


class StructuredGenerateAgent(Agent):
    """Base agent: LLM → pydantic schema → Artifact.

    Declare `schema`, override `build_prompt(inputs)`; optionally
    `fallback(inputs)` for a deterministic fallback variant (§67).
    """

    schema: type[BaseModel] | None = None
    system_prompt: str = SYSTEM_STRUCTURED
    attempts: int = 2
    temperature: float | None = None
    max_tokens: int | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.schema is None:
            raise ValueError(
                f"{type(self).__name__} requires a schema= model for its output"
            )

    def build_prompt(self, inputs: list[Artifact[Any]]) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} must implement build_prompt(inputs)"
        )

    def fallback(self, inputs: list[Artifact[Any]]) -> BaseModel | None:
        return None

    async def run(self, event: Event, context: Context) -> None:
        inputs = self.collect_inputs(context)
        if not inputs or self.schema is None:
            return None
        text = self.build_prompt(inputs)
        result = await structured_llm(
            context.resources.llm,
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
        # A stable id makes a re-run of the same trigger refresh the artifact
        # instead of creating a second model result.
        result_id = f"{type(result).__name__.lower()}:{event.artifact_id}"
        effects.create(result, id=result_id)
        return None


class _ToolAgentBase(Agent):
    """Shared constructor for `LLMAgent` and `HITLLMAgent`."""

    system: str = ""
    tools: Sequence[Tool] | dict[str, Tool] = ()
    max_steps: int = 8
    temperature: float | None = None
    max_tokens: int | None = None

    def _extra_consumes(self, llm_name: str) -> tuple[Consume, ...]:
        return ()

    def _make_tool_step(self, llm_name: str) -> Step[Any]:
        return ToolUse(
            name=llm_name,
            system=self.system,
            tools=self.tools,
            max_steps=self.max_steps,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            deferred_tool_groups=getattr(self, "deferred_tool_groups", ()),
        )

    def __init__(
        self,
        *,
        name: str | None = None,
        steps: Sequence[Step[Any]] | None = None,
        system: str | None = None,
        tools: Sequence[Tool] | dict[str, Tool] | None = None,
        consumes: Sequence[Consume] | None = None,
        max_steps: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Build a tool agent without subclassing.

        The keyword surface mirrors the class attributes, so the common case is
        `HITLLMAgent(name=..., system=..., tools=[...], consumes=[...])`
        instead of a five-line subclass.
        """
        if system is not None:
            self.system = system
        if tools is not None:
            self.tools = tools
        if max_steps is not None:
            self.max_steps = max_steps
        if temperature is not None:
            self.temperature = temperature
        if max_tokens is not None:
            self.max_tokens = max_tokens

        llm_name = name or self.name or self.__class__.__name__.lower()
        user_consumes = list(
            consumes if consumes is not None else (self.consumes or ())
        )
        _require_consumes(self.__class__.__name__, llm_name, user_consumes)
        self.consumes = [
            *user_consumes,
            Consume.by_field(ToolAnswer, "agent", llm_name),
            *self._extra_consumes(llm_name),
        ]
        user_steps = list(steps) if steps is not None else list(self.steps or ())
        self.steps = [self._make_tool_step(llm_name), *user_steps]
        super().__init__(name=name, **kwargs)


class LLMAgent(_ToolAgentBase):
    """Ordinary LLM agent with tools: blocking loop, no HITL.

    Configure it with keywords (`system`, `tools`, `consumes`, and `steps` for
    handling the final `ToolAnswer`); subclassing is only needed when the class
    attributes read better.
    """

    deferred_tool_groups: Sequence[DeferredToolGroup] = ()

    def __init__(
        self,
        *,
        deferred_tool_groups: Sequence[DeferredToolGroup] | None = None,
        **kwargs: Any,
    ) -> None:
        if deferred_tool_groups is not None:
            self.deferred_tool_groups = deferred_tool_groups
        super().__init__(**kwargs)


class HITLLMAgent(_ToolAgentBase):
    """LLM agent with HITL: reactive loop that can ask a human.

    Same as `LLMAgent`, plus: the LLM can return `type:"ask"` — a
    `PendingQuestion` (kind="clarify") is created, the loop waits for the
    answer, and the answer is returned as `Observation(source="user")` for the
    loop to continue.
    """

    max_asks: int = 2
    max_approvals: int = 3
    resume_announce: Callable[[str], str] | None = None

    def __init__(
        self,
        *,
        max_asks: int | None = None,
        max_approvals: int | None = None,
        resume_announce: Callable[[str], str] | None = None,
        **kwargs: Any,
    ) -> None:
        if max_asks is not None:
            self.max_asks = max_asks
        if max_approvals is not None:
            self.max_approvals = max_approvals
        if resume_announce is not None:
            self.resume_announce = resume_announce
        super().__init__(**kwargs)

    def _extra_consumes(self, llm_name: str) -> tuple[Consume, ...]:
        return (
            Consume.by_field(Observation, "agent", llm_name),
            Consume(
                PendingQuestion,
                condition=lambda a: a.data.notes.get("agent") == llm_name,
            ),
        )

    def _make_tool_step(self, llm_name: str) -> Step[Any]:
        return ToolUseHITL(
            name=llm_name,
            system=self.system,
            tools=self.tools,
            max_steps=self.max_steps,
            max_asks=self.max_asks,
            max_approvals=self.max_approvals,
            resume_announce=self.resume_announce,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
