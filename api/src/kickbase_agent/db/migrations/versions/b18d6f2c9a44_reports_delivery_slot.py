"""reports_delivery_slot — atomarer At-most-once-Claim für Discord-Digests

Revision ID: b18d6f2c9a44
Revises: a7e41d20b9c3
Create Date: 2026-09-09 09:35:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b18d6f2c9a44"
down_revision: str | None = "a7e41d20b9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reports") as batch_op:
        batch_op.add_column(sa.Column("delivery_slot", sa.String(length=32), nullable=True))
        batch_op.create_unique_constraint(
            "uq_reports_delivery_slot", ["delivery_slot"]
        )


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch_op:
        batch_op.drop_constraint("uq_reports_delivery_slot", type_="unique")
        batch_op.drop_column("delivery_slot")
