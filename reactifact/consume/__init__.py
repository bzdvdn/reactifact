"""Declarative consume contracts and their variants."""

from .base import Consume
from .correlated import AbsentConsume, CorrelatedConsume, IdempotentConsume, JoinConsume

__all__ = [
    "AbsentConsume",
    "Consume",
    "CorrelatedConsume",
    "IdempotentConsume",
    "JoinConsume",
]
