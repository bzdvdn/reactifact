"""medic-lab produce — pipeline stages, evaluation and steering.

Each module owns one concern (stages / evaluate / steer); this package keeps
the example import surface flat: `from ..steps import Generator, ...`.
"""

from .evaluate import Evaluator
from .stages import (
    ClaimBuilder,
    CrossChecker,
    ExtractEvidence,
    Generator,
    Investigator,
    Resolver,
)
from .steer import Deepen, Reporter, Steer

__all__ = [
    "ClaimBuilder",
    "CrossChecker",
    "Deepen",
    "Evaluator",
    "ExtractEvidence",
    "Generator",
    "Investigator",
    "Reporter",
    "Resolver",
    "Steer",
]
