"""Rückwärtsgang: Gesundheitsprüfung nach dem Selbst-Deploy und automatischer Revert.

Der teuerste Fehler eines selbstverändernden Systems ist nicht der schlechte Commit,
sondern der schlechte Commit, den niemand zurücknimmt. Deshalb trägt jede
Selbstmodifikation einen Marker im Betreff — und genau dieser Marker ist der Griff,
an dem man sie wieder herauszieht.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent import self_deploy as sd
from kickbase_agent.db.models import Base

NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


# ----------------------------------------------------------------- Marker


def test_subject_carries_the_marker_and_stays_readable():
    subject = sd.commit_subject("erwartung", "kickbase_over_p 0.60 -> 0.66")
    assert subject.startswith(sd.SELF_MARKER)
    assert "erwartung" in subject and "0.66" in subject
    assert sd.is_self_commit(subject)


def test_a_human_commit_is_not_a_self_commit():
    assert not sd.is_self_commit("feat(erwartung): Ljubicic-Lehre")


# ------------------------------------------------------- Gesundheitsprüfung


def _healthy() -> sd.HealthSnapshot:
    return sd.HealthSnapshot(
        container_up=True, jobs_registered=24, log_errors=[], failed_jobs=[]
    )


def test_healthy_box_passes():
    result = sd.evaluate_health(_healthy())
    assert result.ok and result.problems == []


def test_container_down_fails():
    snap = _healthy()
    snap.container_up = False
    result = sd.evaluate_health(snap)
    assert not result.ok and any("Container" in p for p in result.problems)


def test_too_few_registered_jobs_fails():
    """Ein Worker, der startet, aber nichts plant, ist der stille Totalausfall."""
    snap = _healthy()
    snap.jobs_registered = 3
    result = sd.evaluate_health(snap)
    assert not result.ok and any("Jobs" in p for p in result.problems)


def test_errors_in_the_log_fail():
    snap = _healthy()
    snap.log_errors = ["Traceback (most recent call last):"]
    result = sd.evaluate_health(snap)
    assert not result.ok and any("Log" in p for p in result.problems)


def test_failed_jobs_from_the_api_fail():
    snap = _healthy()
    snap.failed_jobs = ["Intent-Executor"]
    result = sd.evaluate_health(snap)
    assert not result.ok and any("Intent-Executor" in p for p in result.problems)


def test_unknown_state_is_not_healthy():
    """Fail-closed: Was nicht messbar war, gilt nicht als in Ordnung."""
    result = sd.evaluate_health(sd.HealthSnapshot())
    assert not result.ok
    assert any("nicht messbar" in p for p in result.problems)


# --------------------------------------------------- Snapshot aus Rohdaten


def test_snapshot_reads_container_scheduler_and_api():
    snap = sd.snapshot_from(
        container_ps="kickbase-agent   Up 3 minutes\nkickbase-api   Up 3 minutes",
        worker_log="INFO apscheduler: Added job \"job_Briefing\"\n"
                   "INFO apscheduler: Added job \"job_Snapshot\"\n",
        jobs_payload={"all_ok": True, "problems": []},
    )
    assert snap.container_up is True
    assert snap.jobs_registered == 2
    assert snap.log_errors == [] and snap.failed_jobs == []


def test_snapshot_collects_errors_and_failed_jobs():
    snap = sd.snapshot_from(
        container_ps="kickbase-agent   Exited (1) 10 seconds ago",
        worker_log="Traceback (most recent call last):\nValueError: kaputt\n",
        jobs_payload={"all_ok": False,
                      "problems": [{"label": "Reconcile", "state": "failed"},
                                   {"label": "Puls 09:00", "state": "ok"}]},
    )
    assert snap.container_up is False
    assert snap.log_errors and snap.failed_jobs == ["Reconcile"]


# ----------------------------------------------------------------- Revert


class FakeRun:
    """Protokolliert Kommandos; `fail_on` laesst genau eines scheitern."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        if self.fail_on and self.fail_on in " ".join(cmd):
            return 1, "boom"
        return 0, "ok"


@pytest.mark.asyncio
async def test_rollback_reverts_redeploys_and_alarms(session):
    run = FakeRun()
    posts: list[str] = []
    deploys: list[int] = []

    async def redeploy():
        deploys.append(1)
        return True

    async def post(text, **kw):
        posts.append(text)

    result = await sd.rollback(
        "abc1234", reason="Gesundheitspruefung rot: Container down",
        run=run, redeploy=redeploy, post=post, session=session, now=NOW,
    )

    assert result.reverted and result.sha == "abc1234"
    assert ["git", "revert", "--no-edit", "abc1234"] in run.calls
    assert deploys == [1]
    assert posts and "abc1234" in posts[0]


@pytest.mark.asyncio
async def test_rollback_that_cannot_revert_freezes_and_shouts(session):
    """Wenn nicht einmal das Zurücknehmen klappt, ist Weiterhandeln keine Option."""
    from kickbase_agent.agent import freeze as fz

    run = FakeRun(fail_on="revert")
    posts: list[str] = []

    async def post(text, **kw):
        posts.append(text)

    result = await sd.rollback(
        "abc1234", reason="Deploy rot", run=run, post=post, session=session, now=NOW
    )

    assert not result.reverted and result.problems
    assert fz.is_frozen(session)
    assert posts and "Hand" in posts[0]


@pytest.mark.asyncio
async def test_rollback_without_channel_still_works(session):
    run = FakeRun()
    result = await sd.rollback("abc1234", reason="x", run=run, session=session, now=NOW)
    assert result.reverted


def test_health_alarm_names_the_commit_and_the_problems():
    text = sd.format_health_alarm(
        "abc1234", sd.HealthResult(ok=False, problems=["Container ist nicht oben"])
    )
    assert "abc1234" in text and "Container" in text
