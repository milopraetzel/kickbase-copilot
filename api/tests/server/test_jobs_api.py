"""GET /api/jobs — Heartbeat-Blick des Cron-Wächters (Emils Regel 25.08.)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, JobRun
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

NOW = datetime.now(UTC)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app, headers={"X-Requested-With": "kba"})


def _run(db: Session, label: str, minutes_ago: float, status: str = "ok", error=None) -> None:
    started = NOW - timedelta(minutes=minutes_ago)
    db.add(JobRun(
        label=label, started_at=started,
        finished_at=None if status == "running" else started + timedelta(seconds=2),
        status=status, duration_ms=2000, error=error,
    ))
    db.commit()


def test_empty_is_honest_not_an_error(client):
    body = client.get("/api/jobs").json()
    assert body["count"] == 0 and body["jobs"] == []


def test_shows_last_run_per_label_oldest_first(client, db):
    _run(db, "Reconcile", 3)
    _run(db, "Reconcile", 63)          # aelterer Lauf desselben Jobs
    _run(db, "Briefing", 800)
    jobs = client.get("/api/jobs").json()["jobs"]
    assert [j["label"] for j in jobs] == ["Briefing", "Reconcile"]  # aeltester zuerst
    reconcile = next(j for j in jobs if j["label"] == "Reconcile")
    assert reconcile["age_s"] < 400 and reconcile["runs_window"] == 2


def test_failed_runs_are_counted_and_surfaced(client, db):
    _run(db, "Snapshot", 30, status="failed", error="KickbaseAuthError: Token")
    body = client.get("/api/jobs").json()
    assert body["failed_window_total"] == 1
    job = body["jobs"][0]
    assert job["status"] == "failed" and "KickbaseAuthError" in job["error"]


def test_stale_hint_marks_jobs_older_than_a_day(client, db):
    _run(db, "Briefing", 60 * 30)      # 30 h her
    _run(db, "Reconcile", 5)
    jobs = {j["label"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert jobs["Briefing"]["stale_hint"] is True
    assert jobs["Reconcile"]["stale_hint"] is False


def test_window_limits_counters_but_keeps_last_run(client, db):
    _run(db, "Puls", 60 * 40)          # ausserhalb eines 24-h-Fensters
    body = client.get("/api/jobs?hours=24").json()
    job = body["jobs"][0]
    assert job["runs_window"] == 0 and job["last_run"] is not None
