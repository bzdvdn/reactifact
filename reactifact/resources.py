from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .providers import EmbeddingProvider, LLMProvider
from .sources import Source

if TYPE_CHECKING:
    from .budget import Budget
    from .context_builder import ContextBuilder


class RuntimeResources:
    def __init__(
        self,
        llm: LLMProvider | None = None,
        embedder: EmbeddingProvider | None = None,
        sources: dict[str, Source] | None = None,
        context_builder: ContextBuilder | None = None,
        verification_threshold: float | None = None,
        **additional: Any,
    ):
        self.llm = llm
        self.embedder = embedder
        self.sources = sources or {}
        # Framework-wide pass/fail cutoff for `Verify` (verify.py): `None`
        # means "use Verify's own DEFAULT_THRESHOLD". A `Verify` instance's
        # own explicit `threshold=` still overrides this per agent.
        self.verification_threshold = verification_threshold
        # Runtime-level policy for what actually goes into an agent's inputs
        # (ranking/truncation) — `None` reproduces the old, unranked
        # behavior. See `context_builder.py`; applied inside
        # `Agent._collect_inputs` (`agents.py`), so both the runtime's
        # provenance (`Runtime._collect_reads`) and the agent's actual
        # produce inputs go through the same builder call and stay in sync.
        self.context_builder = context_builder
        self.additional = additional
        # Set by Runtime per turn (not a constructor param — the runtime, not
        # the caller, owns these): the active Budget and its wall-clock
        # deadline, read back by ToolUse's own inner loop (§ tool_use.py) to
        # enforce the tool-call/time budget between its own round-trips, not
        # just at the top-level Runtime._budget_exhausted check.
        self.budget: Budget | None = None
        self.budget_deadline: float | None = None

    def get_source(self, source_id: str) -> Source | None:
        return self.sources.get(source_id)

    def set(self, name: str, value: Any) -> None:
        self.additional[name] = value

    def get(self, name: str) -> Any:
        return self.additional.get(name)

    async def aclose(self) -> None:
        """Closes the llm/embedder clients if they support it.

        Duck-typed: `LLMProvider`/`EmbeddingProvider` don't require `aclose`
        (a fake/no-op test double doesn't need one), so it's called only when
        present. Nothing in the runtime calls this automatically — resources
        are typically shared across many turns/runtimes, and closing them
        early would break whatever still holds a reference. Call it yourself
        once, at real shutdown: a FastAPI `lifespan`, or the end of a script.
        `ChatAssistant` is the one exception — see its docstring.
        """
        for provider in (self.llm, self.embedder):
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()
