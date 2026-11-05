"""intents — Vorhaben der Steuerzentrale (Spec 2026-08-23-steuerzentrale.md)

Revision ID: b7d1c2e3f4a5
Revises: aa220b87e0c1
Create Date: 2026-08-23 14:05:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d1c2e3f4a5'
down_revision: str | None = 'aa220b87e0c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'intents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('player_id', sa.String(length=64), nullable=False),
        sa.Column('player_name', sa.String(length=200), nullable=False),
        sa.Column('team_id', sa.String(length=64), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.Column('listing_type', sa.String(length=16), nullable=False),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('execute_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('emil_note', sa.Text(), nullable=True),
        sa.Column('adjusted_from', sa.BigInteger(), nullable=True),
        sa.Column('executed_action_id', sa.Integer(), nullable=True),
        sa.Column('result', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['executed_action_id'], ['transfer_actions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_intents_kind', 'intents', ['kind'])
    op.create_index('ix_intents_player_id', 'intents', ['player_id'])
    op.create_index('ix_intents_execute_at', 'intents', ['execute_at'])
    op.create_index('ix_intents_status', 'intents', ['status'])


def downgrade() -> None:
    op.drop_index('ix_intents_status', table_name='intents')
    op.drop_index('ix_intents_execute_at', table_name='intents')
    op.drop_index('ix_intents_player_id', table_name='intents')
    op.drop_index('ix_intents_kind', table_name='intents')
    op.drop_table('intents')
