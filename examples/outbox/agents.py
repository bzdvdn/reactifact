"""The outbox demo: agents.

`order_agent` is the ordinary case. `mirror_agent` reuses the same produce so
two agents react to the *same* `Order` event in one generation — snapshot
isolation means both write the intent, and the outbox dedupes them at drain, so
only one notification goes out (see `main.same_generation`).
"""

from reactifact import Consume, create_agent

from .models import Order
from .produce import process_order

order_agent = create_agent(
    "order",
    consumes=[Consume(Order)],
    produces=[process_order],
)
mirror_agent = create_agent(
    "mirror",
    consumes=[Consume(Order)],
    produces=[process_order],
)

AGENTS = [order_agent]
DUPLICATE_AGENTS = [order_agent, mirror_agent]
