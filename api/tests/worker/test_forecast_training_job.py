"""DB-Training aus echten Performancezeilen; kein Zugang zu Handelsfunktionen."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base, Player, PlayerHistory, PlayerPerformance, Report
from kickbase_agent.intel.forecasting.dataset import load_seed
from kickbase_agent.worker import forecast_training_job as job

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "forecast_seed_sample.json"


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'training.sqlite'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def populate(session):
    for pid, player in json.loads(FIXTURE.read_text())["players"].items():
        session.add(Player(id=pid, kickbase_player_id=pid, name=pid, position=3, team="1"))
        for row in player["performance"]:
            session.add(PlayerPerformance(
                player_id=pid, season=row["season"], matchday=row["matchday"],
                points=row["points"], minutes=row["minutes"],
                match_date=datetime.fromisoformat(row["match_date"]),
                captured_at=datetime.fromisoformat(player["performance_fetched_at"]),
                points_observed=row.get("points_observed"),
                minutes_observed=row.get("minutes_observed"),
            ))
    session.commit()


def test_db_export_preserves_unknowns_and_real_capture_normalizes_known_utc(factory, tmp_path):
    with factory() as session:
        populate(session)
        session.add_all([
            PlayerHistory(player_id="2395", recorded_at=NOW - timedelta(days=1),
                          market_value=5_000_000, points=0, is_starter=False),
            PlayerHistory(player_id="2395", recorded_at=NOW + timedelta(days=1),
                          market_value=6_000_000),
        ])
        first = session.scalars(select(PlayerPerformance).order_by(PlayerPerformance.id)).first()
        first.captured_at = NOW + timedelta(days=1)
        session.commit()
        path, audit = job.export_seed(session, as_of=NOW, registry_dir=tmp_path)
    payload = json.loads(path.read_text())
    assert path.name.startswith("db-seed-20260913T120000")
    assert audit["future_capture_excluded"] == 1
    histories = payload["players"]["2395"]["market_history"]
    assert len(histories) == 1
    assert histories[0]["recorded_at"].endswith("+00:00")
    assert "is_starter" not in histories[0]  # historischer Default ist kein Startlabel
    perf = payload["players"]["2395"]["performance"]
    assert perf and perf[0]["captured_at"].endswith("+00:00")
    assert any(r["points_observed"] is None for r in perf)
    observations, _ = load_seed(path, as_of=NOW)
    assert observations
    assert all(r.captured_at < NOW for r in observations)
    assert payload["provenance"]["historical_publication_proven"] is False


@pytest.mark.asyncio
async def test_scheduled_job_trains_real_db_candidate_and_never_changes_active(
    factory, tmp_path, monkeypatch,
):
    with factory() as session:
        populate(session)
    monkeypatch.setattr(job, "SessionLocal", factory)
    registry = tmp_path / "registry"
    registry.mkdir()
    active = registry / "active.json"
    active.write_text('{"preserve_existing_reference":true}')
    before = active.read_bytes()
    result = await job.run_forecast_training_job(now=NOW, registry_dir=registry)
    assert result["status"] == "candidate"
    assert result["activated"] is False
    assert result["promotion_gate"]["passed"] is False
    assert "historical_publication_unverified" in result["promotion_gate"]["reasons"]
    assert result["evaluation"]["test"]["points"]["candidate"]["mae"] >= 0
    assert Path(result["candidate_path"]).exists()
    assert active.read_bytes() == before
    with factory() as session:
        report = session.scalars(select(Report)).one()
        assert report.kind == "model_evaluation"
        assert report.meta["model_version"] == result["model_version"]
        assert "engine_decision" in report.body


@pytest.mark.parametrize("populated", [False, True])
def test_insufficient_or_failed_training_records_error_without_activation(
    factory, tmp_path, monkeypatch, populated,
):
    with factory() as session:
        if populated:
            populate(session)

            def fail(*args, **kwargs):
                raise RuntimeError("Kontrollierter Trainerfehler")

            monkeypatch.setattr(job, "train_candidate", fail)
        result = job.train_from_database(session, as_of=NOW, registry_dir=tmp_path)
        assert result["status"] == ("error" if populated else "skipped")
        assert result["activated"] is False
        assert result["error_type"] == ("RuntimeError" if populated else "ValueError")
        assert session.scalars(select(Report)).one().meta["status"] == result["status"]
    assert not (tmp_path / "active.json").exists()


def test_nonempty_short_history_records_skipped_without_fabricated_metrics(factory, tmp_path):
    with factory() as session:
        populate(session)
        session.query(PlayerPerformance).filter(PlayerPerformance.id > 2).delete()
        session.commit()
        result = job.train_from_database(session, as_of=NOW, registry_dir=tmp_path)
        assert result["export_audit"]["performance_rows"] == 2
        assert result["status"] == "skipped"
        assert "evaluation" not in result
        assert session.scalars(select(Report)).one().meta["activated"] is False
