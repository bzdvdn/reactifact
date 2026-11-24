"""The outbox demo: one agent normally, two for the same-generation case."""

from reactifact import create_step_group

from .steps import process_order

order_group = create_step_group("order", steps=[process_order])
mirror_group = create_step_group("mirror", steps=[process_order])

GROUPS = [order_group]
DUPLICATE_GROUPS = [order_group, mirror_group]
