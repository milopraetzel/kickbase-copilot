"""Cron-Wächter: Ist jeder Scheduler-Job wirklich gelaufen? (Emils Regel 25.08.)

Anlass: „Container läuft" und „Added job" beweisen NICHT, dass ein Job feuert —
die APScheduler-Falle (Sync-Lambda meldete Erfolg, tat nichts) und die TZ-Falle
(alle Crons 2 h zu spät) blieben genau deshalb tagelang unentdeckt. Seit 25.08.
schreibt jeder Lauf einen Heartbeat (`job_runs`), und dieser Wächter rechnet aus
dem ECHTEN Trigger nach, ob ein Job überfällig ist.

Kein Katalog erwarteter Zeiten: Die Erwartung kommt aus dem Trigger selbst
(`trigger.get_next_fire_time(prev, prev)`) — neue Jobs sind damit automatisch
mitüberwacht, ohne dass jemand eine Liste pflegen muss.

Statusbegriffe:
- `ok`        — letzter Lauf erfolgreich, nächste Fälligkeit noch nicht erreicht.
- `overdue`   — seit der erwarteten Feuerzeit ist mehr als die Toleranz vergangen.
- `failed`    — letzter Lauf endete mit Fehler (Job feuert, aber bricht ab).
- `stuck`     — Lauf startete, hat aber nie ein Ende geschrieben (Prozess-Absturz
                mittendrin oder Hänger); erkennbar an `status='running'` + Alter.
- `never_ran` — noch nie gelaufen; erst überfällig, wenn die erste erwartete
                Feuerzeit (nach Registrierung) plus Toleranz vorbei ist.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import JobRun

log = logging.getLogger(__name__)

#: Puffer auf die erwartete Feuerzeit: APScheduler-Jitter (bis 300 s) + Laufzeit
#: langer Jobs (Cockpit-Build ~3 min) dürfen keinen Fehlalarm auslösen.
DEFAULT_TOLERANCE_S = 900

#: Ein Lauf ohne Ende gilt nach dieser Zeit als hängend/abgestürzt.
STUCK_AFTER_S = 3600

#: Job-Präfix aus make_scheduled_job (`runner.__name__ = f"job_{label}"`).
JOB_NAME_PREFIX = "job_"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


# ------------------------------------------------------------------ Heartbeat


def record_start(session: Session, label: str, now: datetime) -> int | None:
    """Lauf-Beginn festhalten; gibt die Zeilen-ID zurück (None bei DB-Problem).

    Fehler beim Schreiben dürfen den Job NIE verhindern — der Heartbeat ist
    Diagnose, nicht Funktion.
    """
    try:
        row = JobRun(label=label, started_at=now, status="running")
        session.add(row)
        session.commit()
        return row.id
    except Exception:  # noqa: BLE001 — Heartbeat darf den Job nicht killen
        log.warning("Heartbeat-Start für %s fehlgeschlagen", label, exc_info=True)
        session.rollback()
        return None


def record_finish(
    session: Session, run_id: int | None, now: datetime, *, error: str | None = None
) -> None:
    """Lauf-Ende festhalten (ok/failed). `run_id=None` (Start misslungen) ⇒ no-op."""
    if run_id is None:
        return
    try:
        row = session.get(JobRun, run_id)
        if row is None:
            return
        started = _as_utc(row.started_at) or now
        row.finished_at = now
        row.status = "failed" if error else "ok"
        row.duration_ms = max(0, int((now - started).total_seconds() * 1000))
        row.error = (error or None) if error is None else error[:2000]
        session.commit()
    except Exception:  # noqa: BLE001
        log.warning("Heartbeat-Ende für Lauf %s fehlgeschlagen", run_id, exc_info=True)
        session.rollback()


# ------------------------------------------------------------------ Auswertung


@dataclass
class JobStatus:
    label: str
    state: str  # ok | overdue | failed | stuck | never_ran
    last_run: datetime | None = None
    last_status: str | None = None
    due_since_s: int | None = None
    next_expected: datetime | None = None
    error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.state == "ok"


@dataclass
class HealthReport:
    checked: int = 0
    problems: list[JobStatus] = field(default_factory=list)
    healthy: list[JobStatus] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return not self.problems


def last_runs(session: Session, labels: Iterable[str]) -> dict[str, JobRun]:
    """Letzter Lauf je Label (unabhängig vom Status)."""
    wanted = list(labels)
    if not wanted:
        return {}
    newest = (
        select(JobRun.label, func.max(JobRun.id).label("max_id"))
        .where(JobRun.label.in_(wanted))
        .group_by(JobRun.label)
        .subquery()
    )
    rows = session.execute(
        select(JobRun).join(newest, JobRun.id == newest.c.max_id)
    ).scalars()
    return {r.label: r for r in rows}


def label_of(job: Any) -> str:
    """Label aus dem APScheduler-Job (`job_<label>`), sonst der Job-Name."""
    name = getattr(job, "name", "") or ""
    return name[len(JOB_NAME_PREFIX):] if name.startswith(JOB_NAME_PREFIX) else name


def _expected_fire(job: Any, after: datetime) -> datetime | None:
    """Wann hätte der Job nach `after` das nächste Mal feuern müssen?"""
    trigger = getattr(job, "trigger", None)
    if trigger is None:
        return None
    try:
        return _as_utc(trigger.get_next_fire_time(after, after))
    except Exception:  # noqa: BLE001 — unbekannter Trigger ⇒ nicht bewertbar
        return None


def evaluate_jobs(
    jobs: Iterable[Any],
    session: Session,
    now: datetime,
    *,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    started_at: datetime | None = None,
) -> HealthReport:
    """Alle registrierten Jobs gegen ihre Heartbeats prüfen.

    `started_at` = Prozessstart: Jobs, die seither noch gar nicht dran waren,
    gelten nicht als überfällig (frischer Container ist kein Ausfall).
    """
    jobs = list(jobs)
    labels = [label_of(j) for j in jobs]
    runs = last_runs(session, labels)
    report = HealthReport(checked=len(jobs))

    for job in jobs:
        label = label_of(job)
        run = runs.get(label)
        status = _evaluate_one(job, label, run, now, tolerance_s, started_at)
        (report.healthy if status.healthy else report.problems).append(status)
    report.problems.sort(key=lambda s: (s.due_since_s or 0), reverse=True)
    return report


def _evaluate_one(
    job: Any,
    label: str,
    run: JobRun | None,
    now: datetime,
    tolerance_s: int,
    started_at: datetime | None,
) -> JobStatus:
    if run is None:
        # Noch nie gelaufen: erst ab der ersten erwarteten Feuerzeit nach
        # Prozessstart (plus Toleranz) ist das ein Problem.
        base = _as_utc(started_at)
        expected = _expected_fire(job, base) if base else None
        if expected is not None and (now - expected).total_seconds() > tolerance_s:
            return JobStatus(
                label=label,
                state="never_ran",
                due_since_s=int((now - expected).total_seconds()),
                next_expected=expected,
            )
        return JobStatus(label=label, state="ok", next_expected=expected)

    started = _as_utc(run.started_at)
    finished = _as_utc(run.finished_at)

    if run.status == "running":
        age = (now - (started or now)).total_seconds()
        if age > STUCK_AFTER_S:
            return JobStatus(
                label=label,
                state="stuck",
                last_run=started,
                last_status=run.status,
                due_since_s=int(age),
            )
        return JobStatus(label=label, state="ok", last_run=started, last_status=run.status)

    expected = _expected_fire(job, finished or started or now)
    overdue_s = None
    if expected is not None:
        delta = (now - expected).total_seconds()
        if delta > tolerance_s:
            overdue_s = int(delta)

    if overdue_s is not None:
        return JobStatus(
            label=label,
            state="overdue",
            last_run=started,
            last_status=run.status,
            due_since_s=overdue_s,
            next_expected=expected,
            error=run.error,
        )
    if run.status == "failed":
        return JobStatus(
            label=label,
            state="failed",
            last_run=started,
            last_status=run.status,
            next_expected=expected,
            error=run.error,
        )
    return JobStatus(
        label=label, state="ok", last_run=started, last_status=run.status, next_expected=expected
    )


# ------------------------------------------------------------------ Bericht


_STATE_TEXT = {
    "overdue": "überfällig",
    "failed": "letzter Lauf fehlgeschlagen",
    "stuck": "hängt (kein Ende gemeldet)",
    "never_ran": "noch nie gelaufen",
}


def _age(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def format_health_report(report: HealthReport, *, always: bool = False) -> str | None:
    """Discord-Text. Ohne Probleme nur bei `always=True` (Morgen-Check)."""
    if report.all_ok:
        if not always:
            return None
        return f"🫀 Cron-Wächter: alle **{report.checked} Jobs** laufen planmäßig."
    lines = [f"🚨 **Cron-Wächter: {len(report.problems)} von {report.checked} Jobs auffällig**"]
    for s in report.problems[:12]:
        detail = _STATE_TEXT.get(s.state, s.state)
        extra = f" seit {_age(s.due_since_s)}" if s.due_since_s else ""
        last = f" · zuletzt {s.last_run:%d.%m. %H:%M}" if s.last_run else " · nie"
        lines.append(f"• **{s.label}** — {detail}{extra}{last}")
        if s.error:
            lines.append(f"  ↳ {s.error[:160]}")
    lines.append(
        "-# Prüfen: `docker logs kickbase-agent | grep -i <job>` · "
        "Neustart holt verpasste Marken nach."
    )
    return "\n".join(lines)


def health_payload(report: HealthReport) -> dict[str, Any]:
    """Maschinenlesbar für API/MCP (`/api/jobs`, box_status)."""

    def one(s: JobStatus) -> dict[str, Any]:
        return {
            "label": s.label,
            "state": s.state,
            "last_run": s.last_run.isoformat() if s.last_run else None,
            "last_status": s.last_status,
            "due_since_s": s.due_since_s,
            "next_expected": s.next_expected.isoformat() if s.next_expected else None,
            "error": s.error,
        }

    return {
        "checked": report.checked,
        "all_ok": report.all_ok,
        "problems": [one(s) for s in report.problems],
        "jobs": [one(s) for s in sorted(report.problems + report.healthy, key=lambda s: s.label)],
    }


#: Zuletzt GEMELDETE Problem-Menge (Labels) — Meldungs-Umbau (Emil, 31.08.):
#: Der stündliche Wächter wiederholte denselben Dauerzustand („2 von 32 Jobs
#: auffällig", 41× in einer Woche). Jetzt meldet er nur STATUSWECHSEL; der
#: Dauerzustand gehört allein in die 07:38-Morgenlage (always=True). In-memory
#: reicht: nach einem Neustart wird einmal neu gemeldet — gewollt.
_last_reported_problems: set[str] | None = None


async def run_job_watchdog(scheduler: Any, channel: Any, *, always: bool = False) -> str:
    """Wächter-Lauf: prüft alle Jobs; meldet Statuswechsel (oder immer, 07:38).

    Rückgabe: 'ok' | 'problems:<n>' — für Tests und Logs.
    """
    from kickbase_agent.db.session import SessionLocal

    global _last_reported_problems
    now = datetime.now(UTC)
    started_at = getattr(scheduler, "kba_started_at", None)
    with SessionLocal() as session:
        report = evaluate_jobs(
            scheduler.get_jobs(), session, now, started_at=started_at
        )
    problems = {s.label for s in report.problems}
    changed = _last_reported_problems is None or problems != _last_reported_problems
    if channel is not None:
        if always or (problems and changed):
            text = format_health_report(report, always=always)
            if text:
                await channel.send_info(text)
                _last_reported_problems = problems
        elif not problems and changed and _last_reported_problems:
            # Übergang zu „alles ok" genau einmal melden — sonst wüsste Emil
            # nie, dass ein gemeldetes Problem sich gelegt hat.
            await channel.send_info("✅ Cron-Wächter: alle Jobs wieder planmäßig.")
            _last_reported_problems = problems
    return "ok" if report.all_ok else f"problems:{len(report.problems)}"


def cutoff_for_retention(now: datetime, days: int = 30) -> datetime:
    """Aufräum-Grenze: Heartbeats älter als `days` werden gelöscht."""
    return now - timedelta(days=days)


def purge_old_runs(session: Session, now: datetime, days: int = 30) -> int:
    """Alte Heartbeats löschen (die Tabelle wächst sonst mit ~500 Zeilen/Tag)."""
    cutoff = cutoff_for_retention(now, days)
    rows = session.query(JobRun).filter(JobRun.started_at < cutoff).delete()
    session.commit()
    return int(rows or 0)


#: Steht wörtlich in der Fehler-Spalte des geschlossenen Laufs — sie wird im
#: Cockpit gelesen, also sagt sie, was passiert ist, nicht bloß „failed".
STUCK_CLOSE_NOTE = "Lauf ohne Endzeile — der Prozess endete, bevor das Ende geschrieben wurde"


def close_stuck_runs(session: Session, now: datetime, *, after_s: int = STUCK_AFTER_S) -> int:
    """Hängende 'running'-Heartbeats schließen — dieselbe Grenze wie der Wächter.

    Fund 31.08.2026: Ein Cockpit-Build vom 28.08. stand seit dem Absturz auf
    'running'. `_evaluate_one` MELDET das korrekt als 'stuck', schließt es aber
    nie — der Lauf blieb drei Tage lang als „läuft gerade" in der Tabelle.

    `after_s` ist bewusst dasselbe `STUCK_AFTER_S`, mit dem der Wächter urteilt:
    zwei verschiedene Grenzen hießen, dass die Anzeige etwas anderes behauptet
    als der Kehrer tut.

    Was hier NICHT passiert: ein `finished_at` erfinden. Wann der Prozess starb,
    weiß niemand; ein geschätzter Endzeitpunkt stünde später als echte Messung
    in der Dauer-Statistik. Eine Lücke ist ehrlicher als eine erfundene Zahl.
    """
    getroffen = 0
    for row in session.query(JobRun).filter(JobRun.status == "running").all():
        started = _as_utc(row.started_at)
        if started is None or (now - started).total_seconds() <= after_s:
            continue
        row.status = "failed"
        row.error = STUCK_CLOSE_NOTE
        getroffen += 1
    if getroffen:
        session.commit()
    return getroffen
