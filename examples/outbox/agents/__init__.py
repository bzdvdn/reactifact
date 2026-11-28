"""The outbox demo: one agent normally, two for the same-generation case."""

from reactifact import Agent, Consume, Pipeline

from ..models import Order
from .order import process_order

order = Agent("order", consumes=[Consume(Order)], steps=[process_order])
mirror = Agent("mirror", consumes=[Consume(Order)], steps=[process_order])

AGENTS = [order]
DUPLICATE_AGENTS = [order, mirror]

#: The reusable definitions each scenario runs.
PIPELINE = Pipeline(agents=AGENTS, name="outbox")
DUPLICATE_PIPELINE = Pipeline(agents=DUPLICATE_AGENTS, name="outbox:duplicate")

__all__ = ["DUPLICATE_PIPELINE", "PIPELINE", "process_order"]
