"""Markt-Scan-Job: Plan berechnen, Vorschläge vormerken, zur Freigabe versenden.

Enthält auch den Neustart-Reload der Approval-Buttons: `reload_approval_views`
registriert beim Bot-Start die persistenten Views aller noch gültigen
'proposed'-Vorschläge neu (Review-Fund: nach einem Worker-Neustart waren alte
Buttons tot, obwohl die Vorschläge in der DB weiter gültig waren).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.models import Proposal
from kickbase_agent.notify.base import ApprovalChannel
from kickbase_agent.notify.formatting import format_proposal


async def stage_plan(
    executor: ActionExecutor, plan: Plan
) -> list[tuple[Proposal, int]]:
    """Alle Vorschläge als 'proposed' vormerken; gibt (Proposal, action_id)-Paare."""
    staged: list[tuple[Proposal, int]] = []
    for proposal in (*plan.buys, *plan.sells):
        action = replace(proposal.action, rationale=proposal.rationale)
        result = await executor.stage(action)
        if result.status == "proposed" and result.action_id is not None:
            staged.append((proposal, result.action_id))
    return staged


async def send_staged(channel: ApprovalChannel, staged: list[tuple[Proposal, int]]) -> int:
    """Vorgemerkte Vorschläge über den Approval-Kanal zustellen."""
    for proposal, action_id in staged:
        await channel.send_approval(format_proposal(proposal, action_id=action_id))
    return len(staged)


def open_approval_ids(session: Session, now: datetime | None = None) -> list[int]:
    """IDs aller noch gültigen 'proposed'-Vorschläge (valid_until in der Zukunft).

    Abgelaufene Vorschläge werden bewusst NICHT geliefert: execute_staged würde
    sie ohnehin als 'expired' ablehnen — eine tote Button-Registrierung wäre
    nur Ballast. Zeilen ohne valid_until (Alt-Bestand vor der TTL-Migration)
    zählen ebenfalls als abgelaufen. Der Zeitvergleich läuft in Python, weil
    SQLite naive Zeitstempel liefert (String-Vergleich gegen aware wäre wacklig).
    """
    stamp = now or datetime.now(UTC)
    rows = session.execute(
        select(TransferAction.id, TransferAction.valid_until).where(
            TransferAction.status == "proposed"
        )
    ).all()
    out: list[int] = []
    for action_id, valid_until in rows:
        if valid_until is None:
            continue
        as_utc = (
            valid_until.replace(tzinfo=UTC)
            if valid_until.tzinfo is None
            else valid_until.astimezone(UTC)
        )
        if as_utc > stamp:
            out.append(action_id)
    return out


async def reload_approval_views(channel) -> int:
    """Bot-Start: persistente Views für offene 'proposed'-Vorschläge registrieren.

    Macht Buttons aus der Zeit VOR dem Neustart wieder bedienbar (die custom_id
    `kba:approve:{id}` identifiziert den Vorschlag; die DB bleibt die Wahrheit
    über TTL und Status). Gibt die Anzahl reaktivierter Views zurück.
    """
    with SessionLocal() as session:
        ids = open_approval_ids(session)
    return channel.register_persistent_views(ids)
