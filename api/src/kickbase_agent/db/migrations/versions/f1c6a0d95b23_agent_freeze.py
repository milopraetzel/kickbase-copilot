"""agent_freeze — Selbst-Einfrieren des Agenten (autonomer Lernzyklus, Stufe A2)

Revision ID: f1c6a0d95b23
Revises: f1a6d0c47e35
Create Date: 2026-08-26 09:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1c6a0d95b23'
# Beim Zusammenfuehren zweier Zweige (26.08.) haengte diese Migration am selben
# Vorgaenger wie f1a6d0c47e35 (learning_evidence) — zwei Alembic-Koepfe, und der
# Container ging in eine Neustart-Schleife. Sie haengt jetzt HINTER der
# Lern-Bilanz-Migration, die zuerst auf der Box lief.
down_revision: str | None = 'f1a6d0c47e35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_freeze",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("readonly_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="manuell"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_freeze_readonly_since", "agent_freeze", ["readonly_since"])


def downgrade() -> None:
    op.drop_index("ix_agent_freeze_readonly_since", table_name="agent_freeze")
    op.drop_table("agent_freeze")
