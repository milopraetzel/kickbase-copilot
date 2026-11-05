"""Migration des Jev-Protokolls: dieselben Spalten wie das Modell, sauber umkehrbar.

Die Suite baut ihr Schema über `Base.metadata.create_all`; auf der Box baut es Alembic
(`alembic upgrade head` beim Workerstart). Weichen beide ab, schreibt das Protokoll auf
der Box ins Leere — `record` schluckt den Fehler, die Jev-Seite bliebe still leer.
"""

from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from kickbase_agent.db.models import JevJudgment


def test_migration_creates_exactly_the_model_columns_and_drops_cleanly():
    migration = import_module("kickbase_agent.db.migrations.versions.2d572992ded3_jev_judgments")
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {c["name"] for c in inspect(connection).get_columns("jev_judgments")}
            assert columns == {c.name for c in JevJudgment.__table__.columns}
            indexes = {i["name"] for i in inspect(connection).get_indexes("jev_judgments")}
            assert {"ix_jev_judgments_created_at", "ix_jev_judgments_kind",
                    "ix_jev_judgments_player_id"} <= indexes
            migration.downgrade()
        assert "jev_judgments" not in inspect(connection).get_table_names()
    engine.dispose()
