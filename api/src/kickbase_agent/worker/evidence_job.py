"""Sammler-Job der Lern-Bilanz: täglich 07:20 Belege sammeln (Spec §4.1).

Er lässt alle Auto-Regeln über das Fenster seit dem letzten Lauf laufen, schreibt die
Funde und meldet **nur, wenn sich ein Status ändert** („L-054 gilt jetzt als
gestützt"). Eine tägliche Meldung „alles wie gestern" wäre Rauschen und würde genau
die Wechsel begraben, um die es geht.

Warum morgens um 07:20: Bis dahin sind die Nacht-Jobs durch (22:05 Snapshot, 22:15
Briefing, 22:20/22:30 Scans, :57 Gebots-Nachschau) — die Spuren des Vortags sind
vollständig, und die Meldung landet vor dem 07:38-Cron-Wächter im Kanal.

Kein Kickbase-Zugriff: Der Job liest ausschließlich die eigene Datenbank.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from kickbase_agent.db.models import JobRun
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.knowledge import evidence as ev
from kickbase_agent.knowledge import evidence_rules as rules
from kickbase_agent.knowledge import status as st
from kickbase_agent.knowledge.learnings import load_learnings

log = logging.getLogger(__name__)

#: Label des Heartbeats (`job_runs`) — auch die Quelle des Zeitfensters.
JOB_LABEL = "Lern-Belege"

#: Beim allerersten Lauf (kein Heartbeat) so weit zurückschauen. Großzügig, weil
#: doppelte Funde die Unique-Sperre abfängt — ein verpasstes Ereignis dagegen
#: bleibt für immer unbelegt.
FIRST_RUN_LOOKBACK_DAYS = 30


def window_start(session: Session, now: datetime) -> datetime:
    """Beginn des Sammelfensters: letzter erfolgreicher Lauf, sonst Rückschau."""
    fallback = now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
    try:
        last = session.execute(
            select(JobRun.started_at)
            .where(JobRun.label == JOB_LABEL, JobRun.status == "ok")
            .order_by(JobRun.started_at.desc())
            .limit(1)
        ).scalars().first()
    except SQLAlchemyError:
        session.rollback()
        return fallback
    if last is None:
        return fallback
    return last.replace(tzinfo=UTC) if last.tzinfo is None else last.astimezone(UTC)


def _statuses(
    session: Session, learnings: list[dict[str, Any]], now: datetime
) -> dict[str, st.Status]:
    ids = [str(e["id"]) for e in learnings if e.get("id")]
    return st.derive_all(
        learnings,
        ev.summaries(session, ids),
        ev.items_for(session, ids),
        now=now,
    )


def format_changes(changes: list[tuple[str, str, st.Status]], titles: dict[str, str]) -> str:
    """Discord-Text: was sich geändert hat und was daraus folgt."""
    lines = ["🧠 **Lern-Bilanz — Status geändert**"]
    for lid, old, new in changes:
        title = titles.get(lid, lid)
        lines.append(f"• **{lid}** {title}: {old} → **{new.status}** ({new.label})")
    if any(new.needs_action for _, _, new in changes):
        lines.append(
            "-# Widerlegt oder wackelnd heißt: Regel im LEARNINGS-Eintrag korrigieren, "
            "nicht weiter darauf handeln."
        )
    return "\n".join(lines)


async def run_evidence_job(channel=None, now: datetime | None = None) -> str:
    """Belege sammeln, Statuswechsel melden. Rückgabe `belege=<n> wechsel=<m>` für Logs."""
    stamp = now or datetime.now(UTC)
    # EINMAL lesen: Vorher- und Nachher-Bild müssen dieselben Behauptungen vergleichen,
    # auch wenn ich die Datei währenddessen anfasse.
    learnings = load_learnings()
    titles = {str(e["id"]): e.get("title", "") for e in learnings if e.get("id")}
    with SessionLocal() as session:
        before = _statuses(session, learnings, stamp)
        since = window_start(session, stamp)
        result = rules.collect(session, since=since, now=stamp)
        after = _statuses(session, learnings, stamp)

    changes = [
        (lid, before[lid].status if lid in before else st.UNBELEGT, state)
        for lid, state in sorted(after.items())
        if lid not in before or before[lid].status != state.status
    ]
    if result.failed:
        log.warning("Sammler: Regeln übersprungen: %s", ", ".join(result.failed))
    for lid, old, new in changes:
        log.info("LERN-STATUS %s: %s -> %s (%s)", lid, old, new.status, new.label)
    if changes and channel is not None:
        await channel.send_info(format_changes(changes, titles))
    return f"belege={result.written} wechsel={len(changes)}"
