"""Vorschlags-Objekte der Decision-Engine."""

from __future__ import annotations

from dataclasses import dataclass

from kickbase_agent.agent.executor import PlannedAction


@dataclass(frozen=True)
class Proposal:
    """Eine geprüfte, regelbasierte Handlungsempfehlung — Eingabe für den Executor."""

    action: PlannedAction
    rule_id: str
    rationale: str
    score: float
    requires_approval: bool
    player_name: str = ""
