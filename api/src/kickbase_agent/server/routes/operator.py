"""DB-Lesesichten für entfernte Agenten; keine Kickbase- oder DB-Schreibzugriffe."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from kickbase_agent.mcp._data import read_audit, read_tips
from kickbase_agent.server.deps import db_session

router = APIRouter()


@router.get("/audit")
def get_audit(
    limit: int = Query(30, ge=0, le=1000),
    status: str | None = None,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Executor-Audit aus der Betriebs-DB; Statusfilter greift vor dem Zeilenlimit."""
    return read_audit(session, limit, status)


@router.get("/tips")
def get_tips(session: Session = Depends(db_session)) -> dict[str, Any]:
    """Tipps aus derselben DB, die der Worker für seine Kauf-Scores verwendet."""
    return read_tips(session)
