"""intent_discord_card — Discord-Veto-Karten je Vorhaben (Spec 2026-08-31)

Revision ID: c4d8f2a61e07
Revises: f1c6a0d95b23
Create Date: 2026-08-31 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4d8f2a61e07'
down_revision: str | None = 'f1c6a0d95b23'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Zwei Spalten am Vorhaben: Message-ID der Discord-Karte und der Stand
    # (updated_at), den die Karte zuletzt gezeigt hat. Eigene Spalten statt
    # sources-JSON, weil der Planner sources bei Updates ueberschreibt.
    # batch_alter_table = SQLite-kompatibel (Muster aa220b87e0c1).
    with op.batch_alter_table('intents') as batch_op:
        batch_op.add_column(sa.Column('discord_msg_id', sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column('discord_card_synced_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('intents') as batch_op:
        batch_op.drop_column('discord_card_synced_at')
        batch_op.drop_column('discord_msg_id')
