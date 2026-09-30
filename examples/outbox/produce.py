"""The produce records the intent; it never performs the I/O.

`effects.act(...)` writes a `PendingAction` — committed state with a stable id
(`action:{key}`) — and returns. Sending the email happens *after* the commit, in
the dispatcher the runtime is given (see `dispatcher.py`), so a replay or a
retry reads the record back instead of sending again.
"""

from __future__ import annotations

from reactifact import PendingAction, ProduceCall, produce

from .models import Order, Receipt


@produce(Receipt, also_creates=[PendingAction])
async def process_order(call: ProduceCall) -> None:
    order = call.trigger
    if order is None or not isinstance(order.data, Order):
        return None

    # The outbound side effect: recorded, not performed. The key is derived from
    # the order id, so a re-run or a merged branch reuses the same intent.
    call.effects.act(
        "notify",
        key=f"notify:{order.data.id}",
        payload={
            "to": order.data.customer,
            "order": order.data.id,
            "total": order.data.total,
        },
    )
    call.effects.upsert(
        Receipt(order_id=order.data.id, text=f"order {order.data.id} confirmed"),
        id=f"receipt:{order.data.id}",
    )
    return None
