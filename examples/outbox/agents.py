"""The outbox demo: one agent normally, two for the same-generation case."""

from reactifact import Agent, Pipeline

from .steps import process_order

order = Agent("order", steps=[process_order])
mirror = Agent("mirror", steps=[process_order])

AGENTS = [order]
DUPLICATE_AGENTS = [order, mirror]

#: The reusable definitions each scenario runs.
PIPELINE = Pipeline(agents=AGENTS, name="outbox")
DUPLICATE_PIPELINE = Pipeline(agents=DUPLICATE_AGENTS, name="outbox:duplicate")
