"""Cron-Wächter (Emils Regel 25.08.): Heartbeats + Überfälligkeits-Rechnung.

Anlass sind zwei echte Vorfälle, die „Container läuft" nicht aufdeckte: das
Sync-Lambda (Job meldete Erfolg, tat nichts) und die TZ-Falle (Crons 2 h zu
spät). Der Wächter leitet die Erwartung aus dem ECHTEN Trigger ab — die Tests
benutzen deshalb echte CronTrigger, keine Attrappen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, JobRun
from kickbase_agent.worker import job_health

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _job(label: str, trigger: CronTrigger) -> SimpleNamespace:
    """APScheduler-Job-Attrappe mit ECHTEM Trigger (name wie make_scheduled_job)."""
    return SimpleNamespace(name=f"job_{label}", trigger=trigger)


def _hourly(label: str = "Reconcile") -> SimpleNamespace:
    return _job(label, CronTrigger(minute=52, timezone=UTC))


def _daily(label: str = "Briefing") -> SimpleNamespace:
    return _job(label, CronTrigger(hour=22, minute=15, timezone=UTC))


def _run(session: Session, label: str, *, started: datetime, status: str, error=None) -> JobRun:
    row = JobRun(
        label=label,
        started_at=started,
        finished_at=None if status == "running" else started + timedelta(seconds=5),
        status=status,
        error=error,
    )
    session.add(row)
    session.commit()
    return row


# ------------------------------------------------------------------ Heartbeat


def test_record_start_and_finish_ok(session):
    run_id = job_health.record_start(session, "Snapshot", NOW)
    assert run_id is not None
    job_health.record_finish(session, run_id, NOW + timedelta(seconds=3))
    row = session.get(JobRun, run_id)
    assert row.status == "ok" and row.duration_ms == 3000 and row.error is None


def test_record_finish_marks_failure_with_error(session):
    run_id = job_health.record_start(session, "Briefing", NOW)
    job_health.record_finish(session, run_id, NOW, error="RuntimeError: API kaputt")
    row = session.get(JobRun, run_id)
    assert row.status == "failed" and "API kaputt" in row.error


def test_record_finish_without_start_is_noop(session):
    job_health.record_finish(session, None, NOW)  # darf nicht werfen
    assert session.query(JobRun).count() == 0


# ------------------------------------------------------------------ Bewertung


def test_recent_run_is_healthy(session):
    _run(session, "Reconcile", started=NOW - timedelta(minutes=8), status="ok")
    report = job_health.evaluate_jobs([_hourly()], session, NOW)
    assert report.all_ok and report.checked == 1


def test_missed_hourly_job_is_overdue(session):
    """Stündlicher Job lief zuletzt vor 3 h → überfällig (die TZ-Falle von damals)."""
    _run(session, "Reconcile", started=NOW - timedelta(hours=3), status="ok")
    report = job_health.evaluate_jobs([_hourly()], session, NOW)
    assert not report.all_ok
    problem = report.problems[0]
    assert problem.state == "overdue" and problem.due_since_s > 3600


def test_failed_last_run_is_reported_even_if_not_overdue(session):
    _run(session, "Briefing", started=NOW - timedelta(minutes=5), status="failed",
         error="KickbaseAuthError")
    report = job_health.evaluate_jobs([_daily()], session, NOW)
    assert report.problems[0].state == "failed"
    assert "KickbaseAuthError" in report.problems[0].error


def test_running_forever_is_stuck(session):
    _run(session, "Cockpit-Build", started=NOW - timedelta(hours=2), status="running")
    job = _job("Cockpit-Build", CronTrigger(minute="22,52", timezone=UTC))
    report = job_health.evaluate_jobs([job], session, NOW)
    assert report.problems[0].state == "stuck"


def test_running_briefly_is_not_stuck(session):
    _run(session, "Cockpit-Build", started=NOW - timedelta(minutes=2), status="running")
    job = _job("Cockpit-Build", CronTrigger(minute="22,52", timezone=UTC))
    assert job_health.evaluate_jobs([job], session, NOW).all_ok


def test_fresh_container_never_ran_is_not_an_alarm(session):
    """Job noch nie gelaufen, Prozess erst seit 2 min: kein Ausfall."""
    report = job_health.evaluate_jobs(
        [_daily()], session, NOW, started_at=NOW - timedelta(minutes=2)
    )
    assert report.all_ok


def test_never_ran_after_expected_window_is_alarm(session):
    """Prozess läuft seit gestern, täglicher Job hätte längst feuern müssen."""
    report = job_health.evaluate_jobs(
        [_daily()], session, NOW, started_at=NOW - timedelta(days=1, hours=2)
    )
    assert report.problems[0].state == "never_ran"


def test_tolerance_covers_jitter(session):
    """5 min nach Fälligkeit ist KEIN Alarm (APScheduler-Jitter bis 300 s)."""
    _run(session, "Reconcile", started=NOW - timedelta(hours=1, minutes=5), status="ok")
    assert job_health.evaluate_jobs([_hourly()], session, NOW).all_ok


def test_multiple_jobs_sorted_by_severity(session):
    _run(session, "Reconcile", started=NOW - timedelta(hours=2), status="ok")
    _run(session, "Briefing", started=NOW - timedelta(days=2), status="ok")
    report = job_health.evaluate_jobs([_hourly(), _daily()], session, NOW)
    assert [p.label for p in report.problems] == ["Briefing", "Reconcile"]


# ------------------------------------------------------------------ Bericht


def test_report_is_silent_when_all_ok(session):
    _run(session, "Reconcile", started=NOW - timedelta(minutes=5), status="ok")
    report = job_health.evaluate_jobs([_hourly()], session, NOW)
    assert job_health.format_health_report(report) is None
    assert "planmäßig" in job_health.format_health_report(report, always=True)


def test_report_names_job_and_age(session):
    _run(session, "Reconcile", started=NOW - timedelta(hours=5), status="ok")
    text = job_health.format_health_report(job_health.evaluate_jobs([_hourly()], session, NOW))
    assert "Reconcile" in text and "überfällig" in text


@pytest.mark.asyncio
async def test_watchdog_posts_only_on_problems(session, monkeypatch):
    posts: list[str] = []

    class Channel:
        async def send_info(self, text: str) -> None:
            posts.append(text)

    monkeypatch.setattr(job_health, "SessionLocal", None, raising=False)
    import kickbase_agent.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _NoCloseSession(session))

    # Zeitbomben-Regel (LEARNINGS 23./24.08., hier am 25.08. erneut zugeschlagen):
    # `run_job_watchdog` rechnet gegen die ECHTE Uhr — Fixtures muessen relativ dazu
    # liegen. Mit dem festen NOW-Anker war der Lauf ab 12:15 UTC ploetzlich
    # ueberfaellig und der Test kippte von selbst.
    real_now = datetime.now(UTC)
    scheduler = SimpleNamespace(
        get_jobs=lambda: [_hourly()], kba_started_at=real_now - timedelta(days=1)
    )
    _run(session, "Reconcile", started=real_now - timedelta(minutes=5), status="ok")
    result = await job_health.run_job_watchdog(scheduler, Channel())
    assert result == "ok" and posts == []


class _NoCloseSession:
    """Session-Wrapper: Kontextmanager, der die Test-Session nicht schließt."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *exc: object) -> None:
        return None


# ------------------------------------------------------------------ Aufräumen


def test_purge_keeps_recent_and_drops_old(session):
    _run(session, "Puls", started=NOW - timedelta(days=40), status="ok")
    _run(session, "Puls", started=NOW - timedelta(days=2), status="ok")
    removed = job_health.purge_old_runs(session, NOW, days=30)
    assert removed == 1 and session.query(JobRun).count() == 1
