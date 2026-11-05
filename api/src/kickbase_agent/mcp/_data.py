"""Gemeinsame DB-Lesesichten für MCP und die Operator-API auf der Box."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction
from kickbase_agent.intel.community import TIP_TTL_DAYS, load_recent_tips
from kickbase_agent.mcp._util import audit_row, iso


def read_audit(session: Session, limit: int, status: str | None) -> dict[str, Any]:
    stmt = select(TransferAction).order_by(
        TransferAction.created_at.desc(), TransferAction.id.desc(),
    )
    if status:
        stmt = stmt.where(TransferAction.status == status)
    actions = [audit_row(row) for row in session.scalars(stmt.limit(max(0, limit)))]
    return {"count": len(actions), "status_filter": status, "actions": actions}


def read_tips(session: Session) -> dict[str, Any]:
    tips = load_recent_tips(session)
    rows = [
        {
            "id": tip.id,
            "player_name": tip.player_name,
            "weight": tip.weight,
            "source": tip.source,
            "note": tip.note,
            "created_at": iso(tip.created_at),
        }
        for tip in sorted(tips, key=lambda tip: tip.created_at, reverse=True)
    ]
    return {"count": len(rows), "ttl_days": TIP_TTL_DAYS, "tips": rows}
