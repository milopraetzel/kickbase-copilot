"""GET /api/jobs — Heartbeats der Scheduler-Jobs (Cron-Wächter, Emils Regel 25.08.).

Die API läuft in einem EIGENEN Container ohne Scheduler — sie kennt die Trigger
nicht und rechnet deshalb keine Überfälligkeit aus (das macht der Wächter im
Worker und meldet nach Discord). Hier steht die Rohwahrheit: Wann lief welcher
Job zuletzt, wie lange, mit welchem Ergebnis. Genau der Blick, den der
Orchestrator in jeder Routine braucht („sind die Crons gelaufen?").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import JobRun
from kickbase_agent.server.deps import db_session

router = APIRouter(tags=["jobs"])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)).isoformat()


def _age_s(dt: datetime | None, now: datetime) -> int | None:
    if dt is None:
        return None
    stamp = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return max(0, int((now - stamp).total_seconds()))


@router.get("/jobs")
def list_jobs(
    hours: int = Query(48, ge=1, le=720),
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Letzter Lauf je Job + Zähler der letzten `hours` Stunden.

    `stale_hint` ist nur ein Hinweis (kein Trigger-Wissen): true, sobald der
    letzte Lauf älter als 24 h ist — tägliche Jobs sind dann fällig, alles
    Häufigere ist sicher auffällig.
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)

    # Zeitlich letzter Lauf je Label (nicht höchste ID — s. job_health.last_runs):
    # nachgetragene Zeilen sollen die Reihenfolge nicht verdrehen.
    newest = (
        select(JobRun.label, func.max(JobRun.started_at).label("max_started"))
        .group_by(JobRun.label)
        .subquery()
    )
    candidates = session.execute(
        select(JobRun).join(
            newest,
            (JobRun.label == newest.c.label) & (JobRun.started_at == newest.c.max_started),
        )
    ).scalars()
    latest: dict[str, JobRun] = {}
    for row in candidates:
        current = latest.get(row.label)
        if current is None or (row.id or 0) > (current.id or 0):
            latest[row.label] = row
    rows = list(latest.values())

    counts: dict[str, dict[str, int]] = {}
    for label, status, n in session.execute(
        select(JobRun.label, JobRun.status, func.count())
        .where(JobRun.started_at >= since)
        .group_by(JobRun.label, JobRun.status)
    ):
        counts.setdefault(label, {})[status] = int(n)

    jobs = []
    for row in rows:
        age = _age_s(row.started_at, now)
        window = counts.get(row.label, {})
        jobs.append(
            {
                "label": row.label,
                "last_run": _iso(row.started_at),
                "last_finished": _iso(row.finished_at),
                "status": row.status,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "age_s": age,
                "stale_hint": bool(age is not None and age > 86_400),
                "runs_window": sum(window.values()),
                "failed_window": window.get("failed", 0),
            }
        )
    jobs.sort(key=lambda j: (j["age_s"] is None, -(j["age_s"] or 0)))
    return {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "count": len(jobs),
        "failed_window_total": sum(j["failed_window"] for j in jobs),
        "jobs": jobs,
    }
