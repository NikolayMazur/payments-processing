from uuid import UUID

from pydantic import BaseModel


class PaymentCreatedEvent(BaseModel):
    """Событие очереди payments.new: платёж принят и ждёт обработки."""

    payment_id: UUID
