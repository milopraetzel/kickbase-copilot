"""transfer_action_valid_until

Revision ID: aa220b87e0c1
Revises: a1c4e9d02b77
Create Date: 2026-08-20 17:31:10.922333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aa220b87e0c1'
down_revision: Union[str, None] = 'a1c4e9d02b77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Freigabe-TTL fuer Vorschlaege: nach valid_until endet ein Approve als
    # 'expired' statt als veralteter Trade. batch_alter_table = SQLite-kompatibel.
    with op.batch_alter_table('transfer_actions') as batch_op:
        batch_op.add_column(sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transfer_actions') as batch_op:
        batch_op.drop_column('valid_until')
