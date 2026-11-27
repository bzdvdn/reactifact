"""Multi-turn: chat memory carries over to a follow-up question.

`BuildAnswer` (`steps/lifecycle.py`) only feeds `conversation_text(...)`
into the LLM prompt — with no model configured, the deterministic fallback
answer ignores it entirely, so a purely `llm=None` run can't show memory
changing the *answer text*. What it can prove, fully deterministically, is
that the memory window itself is correctly populated by the time a
follow-up would need it: this scenario calls the pipeline's own
`conversation_text()` helper against the accumulated `Context` after two
real turns — the exact call `BuildAnswer` makes for the second question.
"""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import AGENTS
from ..models import Answer, Calculation, UserQuery
from ..steps.common import conversation_text
from ._common import GPU_COST_QUESTION, resources

FOLLOW_UP_QUESTION = "how do I set up billing alerts for that?"


@scenario("knowledge: chat memory carries over to a follow-up question (multi-turn)")
async def follow_up_sees_prior_conversation() -> None:
    lab = ScenarioLab(list(AGENTS), resources=resources)
    convo = lab.scenario()

    # turn 1: the flagship deterministic question
    r1 = await convo.turn(UserQuery(text=GPU_COST_QUESTION))
    calc = r1.artifacts(Calculation).exists()
    assert calc.value == 3580
    r1.artifacts(Answer).exists()

    # turn 2: an unrelated follow-up on the same conversation
    r2 = await convo.turn(UserQuery(text=FOLLOW_UP_QUESTION))
    r2.artifacts(Answer).count(2)  # one per turn, nothing overwritten in place

    query2 = r2.context.latest(UserQuery)
    assert query2 is not None
    memory = conversation_text(r2.context, query2.id)
    assert GPU_COST_QUESTION in memory  # turn 1's question is still visible
    assert "3580" in memory  # ... and its answer

    convo.llm.max_calls(0)  # both turns stayed fully deterministic (§67)
