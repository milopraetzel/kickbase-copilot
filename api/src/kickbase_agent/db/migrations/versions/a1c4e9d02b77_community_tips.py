"""community_tips

Revision ID: a1c4e9d02b77
Revises: e73ff9f47bb3
Create Date: 2026-08-21 08:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e9d02b77'
down_revision: Union[str, None] = 'e73ff9f47bb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'community_tips',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('player_name', sa.String(length=200), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_community_tips_player_name'), 'community_tips', ['player_name'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_community_tips_player_name'), table_name='community_tips')
    op.drop_table('community_tips')
