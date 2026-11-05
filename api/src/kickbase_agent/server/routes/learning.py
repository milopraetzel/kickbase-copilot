"""Lern-Schicht-API: Erkenntnisse lesen (GET) und belegen (POST).

- GET  /api/learning            — Treffer-Bilanz, Erkenntnisse, Parameter-Historie
- POST /api/learning/evidence   — EIN Agenten-Beleg zu einer Erkenntnis

Antwort (Vertrag mit dem Cockpit-Tab „Lernen" und dem MCP-Tool ``evaluation``):

    {"now": iso,
     "evaluation": intel.evaluation.evaluate(...),   # buy_hits/shadow/executed/tips/flips/…
     "learnings": knowledge.learnings.load_learnings(),
     "parameters": {"history": [...], "current": {...}},
     "open_calibrations": [str]}

Reine Lesesicht auf die lokale DB + Paketdaten: keine Kickbase-Calls, keine Writes,
keine Secrets. Bei leerer DB kommen Nullwerte (n=0/None), nie ein Fehler.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from kickbase_agent.knowledge import build_learning
from kickbase_agent.knowledge.evidence import record_agent_evidence
from kickbase_agent.server.deps import db_session
from kickbase_agent.server.schemas import LearningEvidenceIn

router = APIRouter()


@router.get("/learning")
def get_learning(session: Session = Depends(db_session)) -> dict[str, Any]:
    """Lern-Schicht: Treffer-Bilanz (Kaufvorschläge echt + Schatten vs. MW nach 1/7 Tagen,
    Tipps, Flips), Erkenntnisse aus LEARNINGS.md, Parameter-Historie + Ist-Werte,
    offene Kalibrierungen."""
    return build_learning(session)


@router.post("/learning/evidence")
def post_learning_evidence(
    payload: LearningEvidenceIn,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Agenten-Beleg schreiben (`source=agent`, Spec §7).

    Kein Kickbase-Write und kein Vorhaben — die Route legt genau EINE Zeile in
    `learning_evidence` an. Ist `ref` für diese Erkenntnis schon bekannt, passiert
    NICHTS und die Antwort sagt das ehrlich (`created: false`) statt 409 zu werfen:
    Ein doppelt gemeldetes Ereignis ist kein Fehler, sondern der Normalfall bei
    überlappenden Fenstern.
    """
    try:
        row = record_agent_evidence(
            session,
            payload.learning_id,
            payload.verdict,
            note=payload.note,
            ref=payload.ref,
            observed_at=payload.observed_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    created = row is not None
    return {
        "evidence": {
            "learning_id": payload.learning_id.strip().upper(),
            "verdict": payload.verdict.strip().lower(),
            "ref": payload.ref.strip(),
            "note": payload.note.strip(),
            "source": "agent",
            "rule_key": "",
            "created": created,
            "observed_at": row.observed_at.isoformat() if row is not None else None,
        },
        "note": (
            "Beleg gespeichert."
            if created
            else "Dieses Ereignis war für diese Erkenntnis schon belegt — nichts angelegt."
        ),
    }
