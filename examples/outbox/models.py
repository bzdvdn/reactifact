from pydantic import BaseModel

# The input that starts the flow. The stable id of the outbound notification is
# derived from `Order.id` — not from a counter or a timestamp — so re-deriving
# it can never produce a second intent.


class Order(BaseModel):
    id: str
    customer: str
    total: float


# The state the produce *does* commit synchronously, next to the intent.
class Receipt(BaseModel):
    order_id: str
    text: str
