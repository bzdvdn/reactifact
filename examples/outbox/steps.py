"""The step records the intent; it never performs the I/O."""

from __future__ import annotations

from reactifact import Artifact, Effects, PendingAction, step

from .models import Order, Receipt


@step(produces=(Receipt, PendingAction), consumes=Order)
async def process_order(order: Artifact[Order], effects: Effects) -> None:
    effects.act(
        "notify",
        key=f"notify:{order.data.id}",
        payload={
            "to": order.data.customer,
            "order": order.data.id,
            "total": order.data.total,
        },
    )
    effects.upsert(
        Receipt(order_id=order.data.id, text=f"order {order.data.id} confirmed"),
        id=f"receipt:{order.data.id}",
    )
