"""incident_commander demo: a scripted offline provider.

`ToolUseHITL`'s own decision loop has no per-call fallback (unlike a plain
`structured_llm` call elsewhere in this repo's examples) — without a model
it just answers "Could not reach a decision." on the very first step, which
would make an offline run of *this* demo skip the whole investigation
(no tool calls, no destructive-tool approval, no evidence). A fixed script
— the same idiom `tests/test_tools.py`'s `ScriptedLLM` uses — keeps the
demo fully offline-runnable while still walking through every harness piece.
Pass a real `LLMProvider` (`build_llm()`) instead for a live run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from reactifact.providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk

#: Commander's decisions, in order: the investigation is already done (its
#: relevant forks, merged — `classify_targets` may have skipped one) by the
#: time Commander runs — it only decides the fix (paused for approval) and
#: reports. The wording doesn't claim anything about a fork that may not
#: have run (e.g. the database), since which forks ran depends on the
#: incident text (`main.py --text`).
COMMANDER_SCRIPT = [
    '{"type":"tool_call","tool":"rollback_deploy","args":{"deploy_id":"checkout-42"}}',
    '{"type":"answer","text":"Rolled back deploy checkout-42 to the previous image — '
    'the new release caused the CrashLoopBackOff. Pods should recover within a minute."}',
]

#: The DBA sub-agent's own decisions, in its own isolated conversation
#: (`agent_tool.AgentAsTool` runs it in a fresh `Context`).
DBA_SCRIPT = [
    '{"type":"tool_call","tool":"check_db_locks","args":{}}',
    '{"type":"answer","text":"No long-running locks, pool healthy — the database is '
    'unlikely to be the root cause."}',
]


class ScriptedLLM(LLMProvider):
    """Answers from a fixed script, one response per `complete()` call."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="")
