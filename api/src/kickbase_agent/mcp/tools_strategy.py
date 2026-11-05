"""Datierte Strategieansicht für Hermes, Codex und andere MCP-Operatoren."""
from __future__ import annotations

import json
from pathlib import Path

from kickbase_agent.config import get_settings
from kickbase_agent.mcp._remote import read_remote
from kickbase_agent.mcp._util import ToolError, tool_guard


@tool_guard
async def strategy_insights(player_id: str = "") -> dict:
    """Bestätigte Liga-Transfers, Finanzierung, Datenlücken und Quellen lesen.

    Ohne player_id: Liga-/Managerbilanz, Historienabdeckung und nächste Prüfschritte.
    Quellenalter, unbekannte Werte und Ausführungsstatus bleiben unverändert erhalten.
    Mit player_id: zusätzlich datierte MW-/Punkte-/Minutenkurven und passende News.
    Quelle ist der letzte vollständige Cockpit-Build, kein neuer Live-Abruf.
    Remote-Modus liest /api/strategy auf der Box; Ausfälle haben keinen lokalen Ersatz.
    """
    data = await read_remote("strategy")
    if data is None:
        target = get_settings().cockpit_html_path
        if not target:
            raise ToolError("Keine Strategiequelle konfiguriert.",
                            hint="Box-API konfigurieren oder Cockpit-Build ausführen.")
        try:
            data = json.loads(Path(target).with_name("strategy.json").read_text())
        except (OSError, ValueError) as exc:
            raise ToolError("Keine lesbare Strategieauswertung vorhanden.",
                            hint="Letzten Cockpit-Build prüfen.") from exc
        data = {**data, "remote": False, "data_source": "local_cockpit_snapshot"}
    result = {key: data.get(key) for key in (
        "version", "generated_at", "status", "remote", "data_source", "summary",
        "portfolio", "manager_stats", "price_buckets", "purchase_premiums",
        "insights", "coverage", "source_refresh", "limitations",
        "deadline", "planning_notes", "news",
        "manager_profiles", "league_history", "action_plan",
        "purchase_reviews", "curation_notes", "team_plan",
    )}
    result["player_count"] = len(data.get("players") or [])
    if player_id:
        player = next((p for p in data.get("players") or []
                       if str(p.get("player_id")) == player_id), None)
        if player is None:
            raise ToolError("Spieler fehlt im Strategie-Snapshot.",
                            hint="ID und Abdeckungsstand prüfen; kein Nullwert eingesetzt.")
        result["player"] = player
        result["news"] = [a for a in (data.get("news") or {}).get("articles", [])
                          if str(a.get("player_id")) == player_id]
        result["trades"] = [t for t in data.get("trades", [])
                            if str(t.get("player_id")) == player_id]
    return result
