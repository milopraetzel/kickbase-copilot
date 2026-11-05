"""GET /api/freeze — Selbst-Einfrieren sehen (Zustand, Grund, seit wann).

Lesend. Das LÖSEN steht bewusst nicht hier: Ein Endpunkt, mit dem der handelnde
Agent seine eigene Notbremse abschaltet, ist keine Freigabe, sondern eine
Sicherung ohne Wirkung. Emils Ziel („wirklich volle Freigabe", 02.09.2026 —
nichts wartet auf einen Menschen) erfüllt stattdessen der SELBST-VERFALL: Der
stündliche Wächter (worker/freeze_job.py) gibt den Freeze frei, sobald der
auslösende Tag vorbei ist UND die Auslöser heute nicht mehr zutreffen
(agent/freeze.release_if_stale). Eine laufende Schieflage friert damit weiter
ein, ein einmaliger schlechter Tag löst sich von allein.
Von Hand geht es weiterhin: `kba unfreeze --by emil`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from kickbase_agent.agent import freeze as fz
from kickbase_agent.server.deps import db_session

router = APIRouter()


def _freeze_json(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {"frozen": False}
    return {
        "frozen": True,
        "since": row.readonly_since.isoformat() if row.readonly_since else None,
        "source": row.source,
        "reason": row.reason,
    }


@router.get("/freeze")
def freeze_status(session: Session = Depends(db_session)) -> dict[str, Any]:
    """Aktueller Einfrier-Zustand — `{"frozen": false}`, wenn alles normal ist."""
    return _freeze_json(fz.active_freeze(session))
