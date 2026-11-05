"""Additive Migration nur gegen eine isolierte In-Memory-Datenbank prüfen."""

from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_evidence_migration_preserves_legacy_values_without_inventing_observation_flags():
    migration = import_module(
        "kickbase_agent.db.migrations.versions.e62c391a5b08_performance_evidence",
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE player_performance "
            "(id INTEGER PRIMARY KEY, points INTEGER, minutes INTEGER)"
        ))
        connection.execute(text("INSERT INTO player_performance VALUES (1, 0, 0)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            row = connection.execute(text("SELECT * FROM player_performance")).mappings().one()
            assert row["points"] == 0 and row["minutes"] == 0
            for key in ("points_observed", "minutes_observed", "is_finished", "captured_at"):
                assert row[key] is None
            migration.downgrade()
        assert {c["name"] for c in inspect(connection).get_columns("player_performance")} == {
            "id", "points", "minutes",
        }
        assert connection.execute(text("SELECT points, minutes FROM player_performance")).one() == (
            0, 0,
        )
    engine.dispose()
