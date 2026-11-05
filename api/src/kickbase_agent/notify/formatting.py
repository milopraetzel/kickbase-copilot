"""Vorschläge und Tagesreports in lesbare, kanalneutrale Nachrichten gießen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kickbase_agent.decision.models import Proposal
from kickbase_agent.notify.base import ChannelMessage

if TYPE_CHECKING:
    from kickbase_agent.cli.plan import Plan

_TITLES = {
    "bid": "🔥 Kaufvorschlag",
    "sell": "💸 Verkaufsvorschlag",
    "accept": "✅ Angebot annehmen?",
    "decline": "🛡️ Angebot ablehnen?",
    "list": "🏷️ Spieler anbieten?",
    "lineup": "⚽ Aufstellung",
}


def _m(value: int | None) -> str:
    return f"{(value or 0) / 1_000_000:.1f}M €"


def format_proposal(proposal: Proposal, action_id: int | None = None) -> ChannelMessage:
    title = _TITLES.get(proposal.action.action_type, "Vorschlag")
    lines = [proposal.rationale, f"Score: {proposal.score * 100:.0f}%"]
    if proposal.requires_approval:
        lines.append("🔐 Über 5M — dein Okay nötig.")
    return ChannelMessage(title=title, body="\n".join(lines), action_id=action_id)


def format_daily_report(plan: Plan) -> str:
    lines = [
        f"📋 Tagesplan Liga {plan.league_id} — Budget {_m(plan.budget)}",
        f"KÄUFE ({len(plan.buys)}):",
    ]
    lines += [f"  • {p.rationale}" for p in plan.buys] or ["  (keine)"]
    lines.append(f"VERKÄUFE ({len(plan.sells)}):")
    lines += [f"  • {p.rationale}" for p in plan.sells] or ["  (keine)"]
    return "\n".join(lines)
