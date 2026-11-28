"""The step records the intent; it never performs the I/O."""

from __future__ import annotations

from reactifact import Artifact, Emit, Outbox, PendingAction, step

from ..models import Order, Receipt


@step(produces=(Receipt, PendingAction), reads=Order)
async def process_order(order: Artifact[Order], outbox: Outbox) -> Emit:
    outbox.act(
        "notify",
        key=f"notify:{order.data.id}",
        payload={
            "to": order.data.customer,
            "order": order.data.id,
            "total": order.data.total,
        },
    )
    return Emit(
        Receipt(order_id=order.data.id, text=f"order {order.data.id} confirmed"),
        id=f"receipt:{order.data.id}",
    )
