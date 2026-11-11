import enum
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.outbox.enums import OutboxStatusEnum
from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _sa_enum(enum_type: type[enum.Enum]) -> tuple[type[enum.Enum], Enum]:
    # Enum'ы храним строкой, а не типом Postgres: новое значение не требует ALTER TYPE.
    return enum_type, Enum(
        enum_type,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    metadata = metadata
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        **dict(
            (
                _sa_enum(CurrencyEnum),
                _sa_enum(PaymentStatusEnum),
                _sa_enum(OutboxStatusEnum),
            ),
        ),
    }


class IDMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, sort_order=-1000)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
