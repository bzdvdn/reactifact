"""The six repair stages, one module per stage (§71).

`examples.repair.steps` re-exports these classes, so both
`from examples.repair.steps import CollectStage` and
`from examples.repair.steps.stages import CollectStage` keep working.
"""

from .approval import ApprovalStage
from .assistant import AssistantStage
from .collect import CollectStage
from .estimate import EstimateStage
from .pick import PickStage
from .plan import PlanStage

__all__ = [
    "ApprovalStage",
    "AssistantStage",
    "CollectStage",
    "EstimateStage",
    "PickStage",
    "PlanStage",
]
