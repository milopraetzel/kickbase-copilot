"""reports — gespeicherte Berichte (Steuerzentrale, Zone Berichte)

Revision ID: c9f3d4e5a6b7
Revises: b7d1c2e3f4a5
Create Date: 2026-08-23 15:05:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c9f3d4e5a6b7'
down_revision: str | None = 'b7d1c2e3f4a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reports_created_at', 'reports', ['created_at'])
    op.create_index('ix_reports_kind', 'reports', ['kind'])


def downgrade() -> None:
    op.drop_index('ix_reports_kind', table_name='reports')
    op.drop_index('ix_reports_created_at', table_name='reports')
    op.drop_table('reports')
