"""Deprecated 0.x module.

`StepGroup` lives in `reactifact.step` now; `Agent` and `create_agent` are the
old names, kept as aliases so 0.x imports keep working. New code should use
`from reactifact import StepGroup, create_step_group`.
"""

from __future__ import annotations

from .step import StepGroup, create_step_group
from .step.group import begin_produce_runs, end_produce_runs, produce_runs

#: Deprecated alias for `StepGroup`.
Agent = StepGroup
#: Deprecated alias for `create_step_group`.
create_agent = create_step_group

__all__ = [
    "Agent",
    "StepGroup",
    "begin_produce_runs",
    "create_agent",
    "create_step_group",
    "end_produce_runs",
    "produce_runs",
]
