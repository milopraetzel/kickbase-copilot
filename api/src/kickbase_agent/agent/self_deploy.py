"""Rückwärtsgang: Marker, Gesundheitsprüfung und automatischer Revert.

Ein System, das sich selbst ändern darf, braucht vor allem eines: einen Griff, an
dem man die Änderung wieder herauszieht — ohne zu raten, welcher Commit gemeint war.
Deshalb trägt **jede** Selbstmodifikation genau EINEN Commit mit dem Marker
``[selbst]`` im Betreff. Der Decision-Record (parameter_history.json) kommt bewusst
als eigener Commit hinterher: Ein Rollback nimmt die Änderung zurück, nicht die
Tatsache, dass sie stattgefunden hat.

Nach dem Deploy wird gemessen, nicht gehofft — vier Fragen, alle fail-closed
(was nicht messbar war, gilt nicht als in Ordnung):

1. Läuft der Worker-Container?
2. Hat der Scheduler seine Jobs registriert? (Ein Worker, der startet und nichts
   plant, ist der stille Totalausfall — genau die APScheduler-Falle vom 25.08.)
3. Stehen Fehler/Tracebacks im Log?
4. Meldet ``/api/jobs`` einen Job als failed/stuck/overdue?

Rot ⇒ ``git revert`` des Markers, Redeploy, Discord-Alarm. Klappt schon das nicht,
wird zusätzlich eingefroren (A2): Wer sich nicht einmal mehr selbst zurücknehmen
kann, soll erst recht kein Geld bewegen.

Die eigentlichen Kommandos (git, ssh, Deploy) kommen als Callables herein — dieses
Modul entscheidet, es fährt nicht. So ist der Rückwärtsgang testbar, ohne dass ein
Test je eine Box anfasst.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: Marker im Commit-Betreff. Kurz, unverwechselbar, `git log --grep`-tauglich.
SELF_MARKER = "[selbst]"

#: So viele registrierte Jobs erwarten wir mindestens nach einem Start. Der Worker
#: plant regulär deutlich über 20; alles unter dieser Marke heißt: Er ist zwar
#: hochgekommen, hat aber seinen Zeitplan nicht aufgebaut.
MIN_REGISTERED_JOBS = 15

#: Zeilen, die im Worker-Log ein echtes Problem anzeigen (nicht jede Warnung).
_LOG_ERROR_RE = re.compile(r"(Traceback \(most recent call last\)|\bERROR\b|\bCRITICAL\b)")
#: Job-Zustände aus job_health, die einen Deploy als misslungen markieren.
_BAD_JOB_STATES = ("failed", "stuck", "overdue", "never_ran")
#: Name des Worker-Containers in `docker compose ps`.
_WORKER_CONTAINER = "kickbase-agent"

#: (returncode, ausgabe) — injizierbar, damit Tests nie git/ssh anfassen.
Runner = Callable[..., tuple[int, str]]


def commit_subject(area: str, summary: str) -> str:
    """Betreff einer Selbstmodifikation: Marker zuerst, dann Bereich und Aussage."""
    return f"{SELF_MARKER} {area}: {summary}".strip()


def is_self_commit(subject: str) -> bool:
    return SELF_MARKER in (subject or "")


# ------------------------------------------------------- Gesundheitsprüfung


@dataclass
class HealthSnapshot:
    """Was nach dem Deploy gemessen wurde. `None` = nicht messbar (nicht „ok")."""

    container_up: bool | None = None
    jobs_registered: int | None = None
    log_errors: list[str] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)


@dataclass
class HealthResult:
    ok: bool
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems)}


def evaluate_health(
    snapshot: HealthSnapshot, *, min_jobs: int = MIN_REGISTERED_JOBS
) -> HealthResult:
    """Vier Fragen, fail-closed beantwortet. Leere Problemliste = grün."""
    problems: list[str] = []
    if snapshot.container_up is None:
        problems.append("Container-Zustand nicht messbar")
    elif not snapshot.container_up:
        problems.append(f"Container {_WORKER_CONTAINER} ist nicht oben")

    if snapshot.jobs_registered is None:
        problems.append("Registrierte Jobs nicht messbar")
    elif snapshot.jobs_registered < min_jobs:
        problems.append(
            f"Nur {snapshot.jobs_registered} Jobs registriert (erwartet >= {min_jobs})"
        )

    if snapshot.log_errors:
        problems.append(f"Fehler im Log: {snapshot.log_errors[0][:120]}")
    for label in snapshot.failed_jobs:
        problems.append(f"Job {label} laeuft nicht sauber")
    return HealthResult(ok=not problems, problems=problems)


def snapshot_from(
    *, container_ps: str | None, worker_log: str | None, jobs_payload: dict | None
) -> HealthSnapshot:
    """Rohdaten der Box in einen Messwert übersetzen.

    Die drei Quellen sind genau die, die es ohne neue Infrastruktur schon gibt:
    `docker compose ps`, `docker logs` und die Antwort von `/api/jobs`.
    """
    snap = HealthSnapshot()
    if container_ps is not None:
        line = next(
            (ln for ln in container_ps.splitlines() if _WORKER_CONTAINER in ln), ""
        )
        low = line.lower()
        snap.container_up = bool(line) and ("up" in low or "running" in low)
    if worker_log is not None:
        snap.jobs_registered = worker_log.count("Added job")
        snap.log_errors = [
            ln.strip() for ln in worker_log.splitlines() if _LOG_ERROR_RE.search(ln)
        ]
    if jobs_payload is not None:
        snap.failed_jobs = [
            str(p.get("label") or "?")
            for p in jobs_payload.get("problems", [])
            if str(p.get("state")) in _BAD_JOB_STATES
        ]
    return snap


def format_health_alarm(sha: str, result: HealthResult) -> str:
    """Discord: was nach dem Selbst-Deploy nicht stimmt."""
    lines = [
        f"🚑 **Selbst-Deploy {sha[:8]} ist rot** — ich nehme ihn zurück.",
        *(f"• {p}" for p in result.problems[:6]),
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------- Revert


@dataclass
class RollbackResult:
    sha: str
    reverted: bool
    problems: list[str] = field(default_factory=list)
    redeployed: bool = False


def _default_run(cmd: Sequence[str], cwd: Any = None) -> tuple[int, str]:
    import subprocess

    proc = subprocess.run(  # noqa: S603 — feste git-Kommandos
        list(cmd), cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def rollback(
    sha: str,
    *,
    reason: str,
    run: Runner | None = None,
    cwd: Any = None,
    redeploy: Callable[[], Awaitable[bool]] | None = None,
    post: Callable[..., Awaitable[Any]] | None = None,
    session: Session | None = None,
    now: datetime | None = None,
) -> RollbackResult:
    """Selbstmodifikation zurücknehmen: `git revert`, Redeploy, Alarm.

    Scheitert schon der Revert, wird eingefroren (A2) und laut gemeldet — dann
    muss ein Mensch ran, und bis dahin fasst der Agent kein Geld mehr an.
    """
    stamp = now or datetime.now(UTC)
    runner = run or _default_run
    result = RollbackResult(sha=sha, reverted=False)

    code, output = runner(["git", "revert", "--no-edit", sha], cwd)
    if code != 0:
        result.problems.append(f"git revert {sha[:8]} fehlgeschlagen: {output.strip()[:200]}")
        if session is not None:
            from kickbase_agent.agent import freeze as fz

            fz.freeze(
                session,
                reason=f"Rueckwaertsgang fehlgeschlagen ({reason})",
                source=fz.SOURCE_DEPLOY,
                now=stamp,
                evidence={"sha": sha, "output": output.strip()[:500]},
            )
        if post is not None:
            await post(
                f"🆘 **Ich komme nicht zurück.** Der Revert von {sha[:8]} ist "
                f"fehlgeschlagen ({reason}). Ich habe mich eingefroren — das muss "
                "jemand von Hand richten."
            )
        return result

    result.reverted = True
    log.warning("Selbstmodifikation %s zurueckgenommen: %s", sha[:8], reason)
    if redeploy is not None:
        try:
            result.redeployed = bool(await redeploy())
        except Exception as exc:  # noqa: BLE001 — Alarm ist wichtiger als der Stacktrace
            result.problems.append(f"Redeploy nach Revert fehlgeschlagen: {exc}")
    if post is not None:
        state = "und neu deployt" if result.redeployed else "(Redeploy offen)"
        await post(
            f"↩️ **Selbstmodifikation {sha[:8]} zurückgenommen** {state}.\n"
            f"Grund: {reason}"
        )
    return result
