"""create payments and outbox

Revision ID: cc47fd4446ea
Revises:
Create Date: 2026-11-11 19:27:09.501756

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "cc47fd4446ea"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SUCCESS", name="outboxstatusenum", native_enum=False, length=32),
            nullable=False,
            comment="Статус отправки",
        ),
        sa.Column("exchange", sa.TEXT(), nullable=False, comment="Обменник RabbitMQ"),
        sa.Column("routing_key", sa.TEXT(), nullable=False, comment="Ключ маршрутизации"),
        sa.Column(
            "event_key", sa.TEXT(), nullable=False, comment="Идентификатор агрегата, к которому относится событие"
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment="Тело события"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True, comment="Момент публикации в брокер"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(
        "ix_outbox_pending_created_at",
        "outbox",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False, comment="Сумма платежа"),
        sa.Column(
            "currency",
            sa.Enum("RUB", "USD", "EUR", name="currencyenum", native_enum=False, length=32),
            nullable=False,
            comment="Валюта",
        ),
        sa.Column("description", sa.TEXT(), server_default="", nullable=False, comment="Описание"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Произвольные данные клиента",
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "succeeded", "failed", name="paymentstatusenum", native_enum=False, length=32),
            nullable=False,
            comment="Статус платежа",
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False, comment="Ключ идемпотентности"),
        sa.Column("request_hash", sa.String(length=64), nullable=False, comment="Хеш параметров запроса на создание"),
        sa.Column("webhook_url", sa.TEXT(), nullable=False, comment="Адрес для уведомления о результате"),
        sa.Column("failure_reason", sa.TEXT(), nullable=True, comment="Причина отказа шлюза"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True, comment="Момент получения итога от шлюза"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("payments")
    op.drop_index("ix_outbox_pending_created_at", table_name="outbox", postgresql_where=sa.text("status = 'PENDING'"))
    op.drop_table("outbox")
