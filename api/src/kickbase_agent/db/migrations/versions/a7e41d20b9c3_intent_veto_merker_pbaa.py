"""intent_veto_merker_pbaa — Veto-Vormerkung + pbaa-Zaehler als Spalten (Review 31.08.)

Revision ID: a7e41d20b9c3
Revises: c4d8f2a61e07
Create Date: 2026-08-31 21:00:00

Zwei Review-Funde desselben Abends:
- veto_requested_at: Ein Veto, das Emil im `executing`-Fenster klickt, lief
  bisher ins Leere (rowcount 0, keine Spur) — der pbaa-Retry belebte das
  Vorhaben danach wieder und der Executor kaufte gegen Emils ausgesprochenes
  Veto. Jetzt wird der Wunsch vorgemerkt und beim Scheitern des laufenden
  Versuchs (oder vor jedem Retry) als `vetoed` eingeloest.
- pbaa_retries: Der Zaehler lag in `sources`, das der Planner bei jedem
  Stunden-Update wholesale ersetzt — das 3er-Budget verdoppelte sich damit
  faktisch. Eigene Spalte nach exakt dem Muster, mit dem dieselbe Migration
  davor `discord_msg_id` begruendet hat.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7e41d20b9c3'
down_revision: str | None = 'c4d8f2a61e07'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('intents') as batch_op:
        batch_op.add_column(
            sa.Column('veto_requested_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column('pbaa_retries', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('intents') as batch_op:
        batch_op.drop_column('pbaa_retries')
        batch_op.drop_column('veto_requested_at')
