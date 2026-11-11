from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import TEXT, CheckConstraint, Index, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.outbox.enums import OutboxStatusEnum
from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum
from app.infra.repositories.sqla.base import Base, CreatedAtMixin, IDMixin


class PaymentOrm(Base, IDMixin, CreatedAtMixin):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), comment="Сумма платежа")
    currency: Mapped[CurrencyEnum] = mapped_column(comment="Валюта")
    description: Mapped[str] = mapped_column(TEXT, server_default="", comment="Описание")
    # Имя metadata занято декларативной базой, поэтому атрибут с подчёркиванием.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default=text("'{}'::jsonb"),
        comment="Произвольные данные клиента",
    )
    status: Mapped[PaymentStatusEnum] = mapped_column(comment="Статус платежа")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, comment="Ключ идемпотентности")
    request_hash: Mapped[str] = mapped_column(String(64), comment="Хеш параметров запроса на создание")
    webhook_url: Mapped[str] = mapped_column(TEXT, comment="Адрес для уведомления о результате")
    failure_reason: Mapped[str | None] = mapped_column(TEXT, comment="Причина отказа шлюза")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), sort_order=100)
    processed_at: Mapped[datetime | None] = mapped_column(comment="Момент получения итога от шлюза", sort_order=100)
    webhook_attempts: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        comment="Сколько раз пытались доставить вебхук",
        sort_order=100,
    )
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(
        comment="Момент, когда получатель подтвердил вебхук",
        sort_order=100,
    )


class OutboxOrm(Base, IDMixin, CreatedAtMixin):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_pending_created_at", "created_at", postgresql_where=text("status = 'PENDING'")),)

    status: Mapped[OutboxStatusEnum] = mapped_column(comment="Статус отправки")
    exchange: Mapped[str] = mapped_column(TEXT, comment="Обменник RabbitMQ")
    routing_key: Mapped[str] = mapped_column(TEXT, comment="Ключ маршрутизации")
    event_key: Mapped[str] = mapped_column(TEXT, comment="Идентификатор агрегата, к которому относится событие")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="Тело события")
    headers: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
        comment="Заголовки сообщения",
    )
    processed_at: Mapped[datetime | None] = mapped_column(comment="Момент публикации в брокер", sort_order=100)
