"""Steuerzentrale-API: Berichte (Zone „Berichte").

Spec: docs/superpowers/specs/2026-08-23-steuerzentrale.md — alles, was der Agent nach
Discord schickt (Briefing, Recherche, Schatten-Scan, Auto-Trading, Planner/Executor,
Gebots-Wächter, Retro …), liegt als Volltext in `reports` (notify/report_store).

- GET /api/reports?kind=&limit=30   Liste, neueste zuerst (Vorschau statt Volltext)
- GET /api/reports/{id}             Einzelbericht MIT Volltext (404 wenn unbekannt)

Reine Lese-Endpunkte: kein Kickbase-Zugriff, keine Schreiboperation. `kind` filtert
exakt (unbekannte Werte liefern eine leere Liste); `delivered` kommt aus meta
(true/false, null wenn der Schreiber es nicht vermerkt hat).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Report
from kickbase_agent.server.deps import db_session

router = APIRouter()

#: Länge der Listen-Vorschau (Zeichen des Volltexts, Zeilenumbrüche → Leerzeichen).
PREVIEW_CHARS = 200
_NEWLINES_RE = re.compile(r"\s*[\r\n]+\s*")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    if dt is None:
        return None
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return aware.isoformat()


def preview_of(body: str, limit: int = PREVIEW_CHARS) -> str:
    """Erste `limit` Zeichen des Volltexts, Zeilenumbrüche (samt Randleerraum) zu Leerzeichen."""
    flat = _NEWLINES_RE.sub(" ", (body or "").strip())
    return flat[:limit]


def _delivered(meta: Any) -> bool | None:
    value = meta.get("delivered") if isinstance(meta, dict) else None
    return value if isinstance(value, bool) else None


def _summary(report: Report) -> dict[str, Any]:
    body = report.body or ""
    return {
        "id": report.id,
        "created_at": _iso(report.created_at),
        "kind": report.kind,
        "title": report.title,
        "source": report.source,
        "chars": len(body),
        "preview": preview_of(body),
        "delivered": _delivered(report.meta),
    }


def _detail(report: Report) -> dict[str, Any]:
    return {**_summary(report), "body": report.body or "", "meta": report.meta}


@router.get("/reports")
def list_reports(
    kind: str | None = Query(None, max_length=32),
    limit: int = Query(30, ge=1, le=200),
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Berichte, neueste zuerst — optional auf eine Berichtsart gefiltert."""
    stmt = select(Report)
    if kind:
        stmt = stmt.where(Report.kind == kind.strip().lower())
    rows = (
        session.execute(stmt.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return {
        "now": _now().isoformat(),
        "count": len(rows),
        "reports": [_summary(r) for r in rows],
    }


@router.get("/reports/{report_id}")
def get_report(report_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    """Einzelbericht mit Volltext (`body`) und meta."""
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Bericht {report_id} nicht gefunden")
    return {"report": _detail(report)}
