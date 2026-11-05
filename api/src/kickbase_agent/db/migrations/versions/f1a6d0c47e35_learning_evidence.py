"""learning_evidence — Belege zu Erkenntnissen (Lern-Bilanz, Spec 2026-08-25)

Revision ID: f1a6d0c47e35
Revises: d4b8c1e9f207
Create Date: 2026-08-26 09:40:00

`learning_id` ist bewusst KEIN Foreign Key: Die Behauptung selbst lebt im Repo
(`LEARNINGS.md`, Anker `[L-051]`), nicht in dieser Datenbank. Die Unique-Sperre
`(learning_id, ref)` sorgt dafür, dass ein Ereignis genau einmal zählt — der
Sammler-Job läuft absichtlich über überlappende Zeitfenster.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a6d0c47e35'
down_revision: str | None = 'd4b8c1e9f207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learning_id", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verdict", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("ref", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("rule_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("learning_id", "ref", name="uq_learning_evidence_ref"),
    )
    op.create_index("ix_learning_evidence_learning_id", "learning_evidence", ["learning_id"])
    op.create_index("ix_learning_evidence_observed_at", "learning_evidence", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_learning_evidence_observed_at", table_name="learning_evidence")
    op.drop_index("ix_learning_evidence_learning_id", table_name="learning_evidence")
    op.drop_table("learning_evidence")
