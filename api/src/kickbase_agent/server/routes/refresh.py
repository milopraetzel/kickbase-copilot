"""POST /api/refresh — Cockpit-Build auf Zuruf (Emils „Aktualisieren"-Button).

Der api-Container teilt Code und Volume mit dem Worker: `run_cockpit_build`
schreibt dieselbe `/app/data/cockpit/index.html`, die nginx ausliefert.
Cooldown über das Datei-Alter (5 min) verhindert Spam; der Build läuft als
Background-Task (~2–3 min, LigaInsider + Spieler-Details hinter dem 1-req/s-
Limiter). Kein Kickbase-Write — nur Lesen + Datei schreiben.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Response

router = APIRouter(tags=["refresh"])
log = logging.getLogger(__name__)

#: Mindestabstand zwischen Builds (Sekunden) — schuetzt Kickbase-API und CPU.
REFRESH_COOLDOWN_S = 300
#: Laufzustand + Ergebnis des letzten Builds. `token` wechselt bei JEDEM fertigen
#: Build — daran erkennt die Seite zuverlässig, dass wirklich neu gebaut wurde
#: (Emils Fund 25.08.: der Button wartete blind 2,5 min und lud dann irgendetwas).
_running: dict[str, object] = {"active": False, "token": 0, "error": None, "started_at": None}


def _html_path() -> Path | None:
    raw = os.environ.get("COCKPIT_HTML_PATH", "")
    return Path(raw) if raw else None


def _age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, datetime.now(UTC).timestamp() - path.stat().st_mtime)
    except OSError:
        return None


async def _build() -> None:
    from kickbase_agent.worker.cockpit_job import run_cockpit_build

    try:
        result = await run_cockpit_build()
        log.info("Refresh-Build fertig: %s", result)
        _running["error"] = None
    except Exception as exc:  # noqa: BLE001 — Fehler NICHT verschlucken: die Seite
        # muss den Grund zeigen können, statt den Nutzer ins Leere warten zu lassen.
        log.exception("Refresh-Build fehlgeschlagen")
        _running["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _running["active"] = False
        _running["token"] = int(_running.get("token") or 0) + 1


@router.get("/refresh/status")
def refresh_status() -> dict:
    """Baut gerade jemand? Wie alt ist die Seite? Lief der letzte Build durch?

    Die Seite pollt das nach einem Klick, statt blind zu warten: fertig ist
    fertig (`running=false` + neuer `token`), Fehler werden sichtbar.
    """
    path = _html_path()
    age = _age_seconds(path) if path is not None else None
    return {
        "running": bool(_running.get("active")),
        "token": int(_running.get("token") or 0),
        "age_s": None if age is None else int(age),
        "error": _running.get("error"),
        "cooldown_s": REFRESH_COOLDOWN_S,
        "target_configured": path is not None,
    }


@router.post("/refresh")
async def trigger_refresh(background: BackgroundTasks, response: Response) -> dict:
    """Frischen Cockpit-Build anstossen (Cooldown 5 min, ein Build gleichzeitig)."""
    path = _html_path()
    if path is None:
        response.status_code = 503
        return {"detail": "COCKPIT_HTML_PATH nicht gesetzt — kein Build-Ziel."}
    if _running.get("active"):
        response.status_code = 429
        return {"detail": "Ein Build läuft bereits — gleich ist die Seite frisch.",
                "running": True, "token": int(_running.get("token") or 0)}
    age = _age_seconds(path)
    if age is not None and age < REFRESH_COOLDOWN_S:
        response.status_code = 429
        return {
            "detail": (
                f"Die Seite ist erst {int(age)} s alt — Cooldown {REFRESH_COOLDOWN_S} s. "
                "Einfach neu laden."
            ),
            "age_s": int(age),
            # `fresh` sagt der Seite: NICHT warten, sofort neu laden — die Datei
            # ist ja aktuell (vorher wartete der Button hier sinnlos 2,5 min).
            "fresh": True,
            "token": int(_running.get("token") or 0),
        }
    # Live-Panels (Gebote/Feed) haben einen 60-s-Modulcache — beim bewussten
    # „Aktualisieren" muss auch der weg, sonst zeigt die frische Seite alte Gebote.
    try:
        from kickbase_agent.server.routes.live import reset_caches

        reset_caches()
    except Exception:  # noqa: BLE001 — Cache-Leeren ist Komfort, kein Muss
        log.warning("Live-Cache konnte nicht geleert werden", exc_info=True)
    _running["active"] = True
    _running["error"] = None
    _running["started_at"] = datetime.now(UTC).isoformat()
    background.add_task(_build)
    return {"started": True,
            "note": (
                "Build läuft (~2–3 min) — die Seite lädt automatisch neu, "
                "sobald er fertig ist."
            ),
            "token": int(_running.get("token") or 0),
            "age_s": None if age is None else int(age)}
