from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.domain.outbox.enums import OutboxStatusEnum


class Outbox(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: OutboxStatusEnum = OutboxStatusEnum.PENDING
    exchange: str
    routing_key: str
    event_key: str
    payload: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None = None
