"""Agents: the reaction unit and the steps they run.

`Agent` declares what wakes it (`consumes`) and owns its ordered `steps`.
`Step` is the work unit inside it; `@agent` is the one-function shortcut.
"""

from ..read import Read
from .agent import Agent
from .agent import StepGroup as StepGroup
from .agent import create_step_group as create_step_group
from .decorator import agent
from .step import (
    Drop,
    Edit,
    Emit,
    Ensure,
    Latest,
    Relate,
    Scope,
    Step,
    StepCall,
    Unscoped,
    Wakes,
    When,
)

# `StepGroup` and `create_step_group` stay importable for 0.x compatibility,
# but are intentionally not in `__all__`: new code uses `Agent`.

__all__ = [
    "Agent",
    "Drop",
    "Edit",
    "Emit",
    "Ensure",
    "Latest",
    "Read",
    "Relate",
    "Scope",
    "Step",
    "StepCall",
    "Unscoped",
    "Wakes",
    "When",
    "agent",
]
