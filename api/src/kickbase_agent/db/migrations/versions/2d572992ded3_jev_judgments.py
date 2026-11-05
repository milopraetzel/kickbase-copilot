"""Jev-Protokoll: jede gestellte Jev-Frage mit Antwort, Regel und Folge.

Spec docs/superpowers/specs/2026-10-01-jev-dashboard-design.md. Neue Tabelle, keine
Änderung an bestehenden Daten.
"""

import sqlalchemy as sa
from alembic import op

revision = "2d572992ded3"
down_revision = "e62c391a5b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jev_judgments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(32), nullable=False, server_default=""),
        sa.Column("batch_id", sa.String(16), nullable=False, server_default=""),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("player_id", sa.String(64), nullable=True),
        sa.Column("player_name", sa.String(200), nullable=True),
        sa.Column("club", sa.String(100), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("answer", sa.String(32), nullable=True),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("probabilities", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("rule", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(32), nullable=False, server_default=""),
        sa.Column("error", sa.String(64), nullable=True),
    )
    op.create_index("ix_jev_judgments_created_at", "jev_judgments", ["created_at"])
    op.create_index("ix_jev_judgments_kind", "jev_judgments", ["kind"])
    op.create_index("ix_jev_judgments_player_id", "jev_judgments", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_jev_judgments_player_id", table_name="jev_judgments")
    op.drop_index("ix_jev_judgments_kind", table_name="jev_judgments")
    op.drop_index("ix_jev_judgments_created_at", table_name="jev_judgments")
    op.drop_table("jev_judgments")
