"""job_runs — Heartbeat je Scheduler-Job (Cron-Wächter, Emils Regel 25.08.)

Revision ID: d4b8c1e9f207
Revises: c9f3d4e5a6b7
Create Date: 2026-08-25 11:20:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd4b8c1e9f207'
down_revision: str | None = 'c9f3d4e5a6b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_runs_label", "job_runs", ["label"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_started_at", table_name="job_runs")
    op.drop_index("ix_job_runs_label", table_name="job_runs")
    op.drop_table("job_runs")
