"""add webhook delivery fields outbox headers and amount check

Revision ID: 3a5add33733f
Revises: cc47fd4446ea
Create Date: 2026-11-11 22:11:51.019593

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3a5add33733f"
down_revision: str | None = "cc47fd4446ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "outbox",
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Заголовки сообщения",
        ),
    )
    op.add_column(
        "payments",
        sa.Column(
            "webhook_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Сколько раз пытались доставить вебхук",
        ),
    )
    op.add_column(
        "payments",
        sa.Column(
            "webhook_delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Момент, когда получатель подтвердил вебхук",
        ),
    )

    # Alembic не замечает CHECK при автогенерации, поэтому ограничение добавлено руками.
    op.create_check_constraint(op.f("ck_payments_amount_positive"), "payments", "amount > 0")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("ck_payments_amount_positive"), "payments", type_="check")
    op.drop_column("payments", "webhook_delivered_at")
    op.drop_column("payments", "webhook_attempts")
    op.drop_column("outbox", "headers")
