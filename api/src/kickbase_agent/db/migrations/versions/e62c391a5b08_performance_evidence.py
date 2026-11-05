"""Feldpräsenz und Abrufzeit von Leistungslabels erhalten.

Alte Nullen bleiben unbekannt; keine rückwirkende Erfindung von Feldpräsenz.
"""

import sqlalchemy as sa
from alembic import op

revision = "e62c391a5b08"
down_revision = "b18d6f2c9a44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("points_observed", "minutes_observed", "is_finished"):
        op.add_column("player_performance", sa.Column(name, sa.Boolean(), nullable=True))
    op.add_column("player_performance", sa.Column(
        "captured_at", sa.DateTime(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    with op.batch_alter_table("player_performance") as batch:
        for name in ("captured_at", "is_finished", "minutes_observed", "points_observed"):
            batch.drop_column(name)
