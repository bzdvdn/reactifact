"""ledger — one produce per formula, each declaring exactly which facts it
depends on via `consumes`. Editing a fact fires `ARTIFACT_UPDATED` only for
that fact's type (`reactifact.context.Context.update`, §41/§42: a no-op edit
doesn't even fire an event) — a formula that doesn't consume it is never
invoked, not merely "invoked but decides not to recompute." No router, no
`if name == "tax_rate"` branch anywhere: the dependency lives in the
`Consume(...)` list, next to the formula it feeds.
"""

from __future__ import annotations

from reactifact import ProduceCall, produce

from .models import Discount, DiscountRate, Hours, LaborCost, Rate, Tax, TaxRate, Total


@produce(LaborCost)
async def compute_labor_cost(call: ProduceCall) -> None:
    context = call.context
    hours = context.latest(Hours)
    rate = context.latest(Rate)
    if hours is None or rate is None:
        return None
    call.effects.upsert(
        LaborCost(value=hours.data.value * rate.data.value), id="labor_cost"
    )


@produce(Tax)
async def compute_tax(call: ProduceCall) -> None:
    context = call.context
    labor_cost = context.latest(LaborCost)
    tax_rate = context.latest(TaxRate)
    if labor_cost is None or tax_rate is None:
        return None
    call.effects.upsert(
        Tax(value=labor_cost.data.value * tax_rate.data.value), id="tax"
    )


@produce(Discount)
async def compute_discount(call: ProduceCall) -> None:
    context = call.context
    labor_cost = context.latest(LaborCost)
    discount_rate = context.latest(DiscountRate)
    if labor_cost is None or discount_rate is None:
        return None
    call.effects.upsert(
        Discount(value=labor_cost.data.value * discount_rate.data.value),
        id="discount",
    )


@produce(Total)
async def compute_total(call: ProduceCall) -> None:
    context = call.context
    labor_cost = context.latest(LaborCost)
    tax = context.latest(Tax)
    discount = context.latest(Discount)
    if labor_cost is None or tax is None or discount is None:
        return None
    call.effects.upsert(
        Total(value=labor_cost.data.value + tax.data.value - discount.data.value),
        id="total",
    )
