"""The outbox demo: one agent normally, two for the same-generation case."""

from reactifact import create_agent

from .steps import process_order

order_agent = create_agent("order", steps=[process_order])
mirror_agent = create_agent("mirror", steps=[process_order])

AGENTS = [order_agent]
DUPLICATE_AGENTS = [order_agent, mirror_agent]
