from pydantic import BaseModel


class Order(BaseModel):
    id: str
    customer: str
    total: float


class Receipt(BaseModel):
    order_id: str
    text: str
