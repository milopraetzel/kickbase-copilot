"""GET /api/strategy — dieselbe datierte Analyse für Cockpit und Operator."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from kickbase_agent.config import get_settings

router = APIRouter(tags=["strategy"])


@router.get("/strategy")
def strategy_snapshot(response: Response) -> dict:
    target = get_settings().cockpit_html_path
    if not target:
        raise HTTPException(503, "Strategieansicht ist noch nicht konfiguriert.")
    try:
        path = Path(target).with_name("strategy.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("generated_at"):
            raise ValueError("Strategie-Snapshot unvollständig")
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Noch keine gültige Strategieauswertung vorhanden.") from exc
    response.headers["Cache-Control"] = "no-store"
    data["served_at"] = datetime.now(UTC).isoformat()
    return data
