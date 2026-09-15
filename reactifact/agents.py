from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from typing import Any

from .artifacts import Artifact
from .consume import Consume
from .context import Context
from .events import Event
from .patches import Patch
from .produce import Produce
from .triggers import Trigger


class Agent(ABC):  # noqa: B024 — interface without abstract methods, run() has a default
    """Base container: consumes some artifacts and produces others.

    If `run` is not overridden, automatically collects inputs according to
    consumes and calls produce on every produce, merging the patches.
    """

    name: str = ""
    consumes: Sequence[Consume] | None = None
    produces: Sequence[Produce[Any]] | None = None
    #: Declarative capability labels (§25), consumed by the adaptive policy.
    capabilities: tuple[str, ...] = ()
    triggers: list[Trigger] = []
    # Run priority within a single generation: lower value runs earlier.
    # Useful for "finishers"/evaluators that logically run last (§24).
    priority: int = 0
    # Max parallel executions of this agent within a generation. Leave None for
    # the runtime default (max_concurrency). Use it to throttle LLM-bound
    # producers (rate limits) independently of cheap I/O (file reads).
    concurrency_limit: int | None = None

    def __init__(
        self,
        name: str | None = None,
        triggers: list[Trigger] | None = None,
        priority: int | None = None,
    ):
        self.name = name or self.name or self.__class__.__name__

        self.priority = (
            priority if priority is not None else getattr(self.__class__, "priority", 0)
        )

        if triggers is not None:
            self.triggers = list(triggers)
        elif self.triggers:
            self.triggers = list(self.triggers)
        elif self.consumes is not None:
            self.triggers = self._generate_triggers_from_consumes()
        else:
            self.triggers = []

        self._validate_contracts()

    def _generate_triggers_from_consumes(self) -> list[Trigger]:
        result = []
        for c in self.consumes or []:
            result.extend(c.to_triggers())
        return result

    def _validate_contracts(self) -> None:
        if self.consumes is not None:
            for c in self.consumes:
                if not isinstance(c, Consume):
                    raise TypeError(
                        f"Agent {self.name!r}: consumes must contain Consume "
                        f"instances, got {type(c)}"
                    )
        if self.produces is not None:
            for p in self.produces:
                if not isinstance(p, Produce):
                    raise TypeError(
                        f"Agent {self.name!r}: produces must contain Produce "
                        f"instances, got {type(p)}"
                    )

    def matches(self, event: Event, context: Context | None = None) -> bool:
        return any(trigger.matches(event, context) for trigger in self.triggers)

    def collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Public access to the consumed artifacts.

        Used by the runtime to record the reads linkage (provenance) on run.
        """
        return self._collect_inputs(context)

    def _collect_inputs(self, context: Context) -> list[Artifact[Any]]:
        """Collects all artifacts matching consumes and conditions.

        Ranking/truncation, if any, is a `Runtime`-level policy
        (`context.resources.context_builder`, see `context_builder.py`), not
        this agent's — applied here so both this and `collect_inputs()`
        (used by the runtime for provenance) see the identical, already
        built list.
        """
        if not self.consumes:
            return []
        inputs: list[Artifact[Any]] = []
        for c in self.consumes:
            artifacts = context.list_artifacts(c.artifact_type)
            if c.condition:
                artifacts = [a for a in artifacts if c.condition(a)]
            inputs.extend(artifacts)
        builder = context.resources.context_builder
        if builder is not None:
            inputs = builder.build(context, self, inputs)
        return inputs

    async def run(self, event: Event, context: Context) -> Patch | None:
        """Default: runs `self.produces` via `execute()` (the effects-first
        path — write a `Produce` subclass or `@produce` function instead of
        overriding this).

        Overriding `run()` to return a `Patch` by hand is a low-level,
        internal escape hatch for cases `effects` genuinely can't express —
        not a third everyday produce style (see `reactifact.produce`'s module
        docstring for the two you should reach for first). No example in
        this repo overrides it; only this repo's own tests do.
        """
        await self.execute(context, event)
        return None

    async def execute(self, context: Context, event: Event | None = None) -> None:
        """Runs the agent's produces (usually on an event).

        Effects-first (§24): produces write `self.effects.*` and return None;
        the *runtime* compiles the effect slot into one atomic patch. This method
        only *runs* the produces — it does not build a patch. (`run` remains the
        agent-level escape hatch for custom Agent subclasses that assemble a
        change-set by hand; the runtime merges its result after the effects.)
        """
        if not self.produces:
            return None
        inputs = self._collect_inputs(context)
        for p in self.produces:
            await p.produce(context, inputs, event)
        return None


def create_agent(
    name: str,
    *,
    consumes: Sequence[Consume] | None = None,
    produces: Sequence[Produce[Any]] | None = None,
    capabilities: tuple[str, ...] = (),
    priority: int = 0,
    concurrency_limit: int | None = None,
    triggers: list[Trigger] | None = None,
) -> Agent:
    """Builds an Agent instance without subclassing.

    `Agent` is a container — nothing needs overriding in the common case — so a
    subclass is only ceremony. This constructor-style builder covers all of the
    declarative knobs:

    ```
    echo = create_agent(
        name="echo",
        consumes=[Consume(Question)],
        produces=[echo_produce],          # a Produce or @produce(...) function
    )
    ```

    Falls back to `name` defaults the same way as `Agent.__init__`.
    """
    agent = Agent(
        name=name, triggers=triggers if triggers is not None else [], priority=priority
    )
    if consumes is not None:
        agent.consumes = consumes
        if triggers is None:
            agent.triggers = agent._generate_triggers_from_consumes()
    if produces is not None:
        agent.produces = produces
    agent.capabilities = capabilities
    agent.concurrency_limit = concurrency_limit
    agent._validate_contracts()
    return agent
