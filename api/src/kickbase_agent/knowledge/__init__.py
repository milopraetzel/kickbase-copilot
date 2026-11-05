"""Lern-Schicht der Steuerzentrale: Treffer-Bilanz + Erkenntnisse + Parameter-Historie.

Ehrliche Lage (Emils Frage 23.08.): die Engine ist regelbasiert mit festen Gewichten;
gelernt wird bisher von Hand (LEARNINGS.md, Parameter-Änderungen mit Commit). Seit
26.08. kommt die Lern-Bilanz dazu: Jede Erkenntnis mit stabiler ID trägt eine
Beleg-Bilanz und daraus einen Status (`knowledge/status.py`) — damit kann ein Eintrag
zum ersten Mal sagen, ob er sich bewährt hat. Dieses Paket macht MESSUNG und
NACHVOLLZIEHBARKEIT als Daten verfügbar — eine Quelle für
``GET /api/learning`` (server/routes/learning.py) und das MCP-Tool ``evaluation``
(mcp/tools_read.py), beide liefern exakt ``build_learning()``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from kickbase_agent.intel.evaluation import evaluate
from kickbase_agent.knowledge import evidence as _evidence
from kickbase_agent.knowledge import status as _status
from kickbase_agent.knowledge.learnings import load_learnings
from kickbase_agent.knowledge.parameters import (
    current_parameters,
    load_parameter_history,
    open_calibrations,
)
from kickbase_agent.knowledge.self_improvement import load_self_improvements

__all__ = [
    "build_learning",
    "enrich_learnings",
    "current_parameters",
    "evaluate",
    "load_learnings",
    "load_parameter_history",
    "open_calibrations",
]


def enrich_learnings(
    session: Session, learnings: list[dict[str, Any]], now: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Erkenntnisse um ihre Beleg-Bilanz ergänzen (Lern-Bilanz, Spec §5/§7).

    Rückgabe: (angereicherte Einträge, Wochen-Kopf `bilanz`, Korrektur-Liste `to_fix`).

    Ein Eintrag OHNE ID bekommt `status=None` und leere Zähler — er hat keinen Anker,
    an dem Belege hängen könnten, und darf deshalb auch keine Bilanz vortäuschen.
    """
    ids = [str(e["id"]) for e in learnings if e.get("id")]
    summaries = _evidence.summaries(session, ids)
    items = _evidence.items_for(session, ids)
    week = _evidence.summaries(
        session, ids, since=now - timedelta(days=_status.WEEK_DAYS)
    )
    statuses = _status.derive_all(learnings, summaries, items, now=now)

    enriched: list[dict[str, Any]] = []
    for entry in learnings:
        lid = entry.get("id")
        state = statuses.get(str(lid)) if lid else None
        summary = summaries.get(str(lid), {}) if lid else {}
        enriched.append(
            {
                **entry,
                "status": state.as_dict() if state is not None else None,
                "evidence": {
                    "confirm": int(summary.get("confirm") or 0),
                    "refute": int(summary.get("refute") or 0),
                    "auto": int(summary.get("auto") or 0),
                    "agent": int(summary.get("agent") or 0),
                    "last_at": summary.get("last_at"),
                },
                "evidence_items": items.get(str(lid), []) if lid else [],
            }
        )

    by_id = {str(e["id"]): e for e in enriched if e.get("id")}
    to_fix = [by_id[lid] for lid in _status.to_fix_ids(statuses) if lid in by_id]
    return enriched, _status.balance(learnings, statuses, week, now=now), to_fix


def build_learning(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """Gesamtbild der Lern-Schicht (Vertrag mit UI/API/MCP):

    {"now": iso, "evaluation": evaluate(...), "learnings": [...],
     "parameters": {"history": [...], "current": {...}}, "open_calibrations": [str],
     "bilanz": {...}, "to_fix": [...], "selbstverbesserungen": [...]}

    Jeder Eintrag in `learnings` trägt seit 26.08. zusätzlich `id`, `art`, `status`,
    `evidence` (Zähler) und `evidence_items` (max. 10, neueste zuerst); `bilanz` ist
    der Wochen-Kopf, `to_fix` die Liste, die zum Handeln auffordert.
    """
    stamp = now if now is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    learnings, bilanz, to_fix = enrich_learnings(session, load_learnings(), stamp)
    return {
        "now": stamp.isoformat(),
        "evaluation": evaluate(session, now=stamp),
        "learnings": learnings,
        "parameters": {
            "history": load_parameter_history(),
            "current": current_parameters(),
        },
        "open_calibrations": open_calibrations(),
        "bilanz": bilanz,
        "to_fix": to_fix,
        # Emils Tabelle (26.08.): jede Aenderung, die der Agent an sich selbst
        # vorgenommen hat — Parameter-Records und Urteile des Lernzyklus in EINER
        # Zeitleiste. Gehoert in dieselbe Antwort, sonst faellt die Karte beim
        # Nachladen aus der Seite (eine verschwindende Anzeige ist schlimmer als
        # eine veraltete).
        "selbstverbesserungen": load_self_improvements(session),
    }


# ------------------------------------------------------------- Entscheidungs-Feed


def decision_entries(limit: int = 60) -> list[dict[str, Any]]:
    """KI-Entscheidungen als Protokoll-Einträge (Emil 23.08.: „Prozess nachvollziehen —
    wie ein Newsfeed"): Parameter-Änderungen (`parameter_history.json`, kind ``decision``)
    und Lektionen (LEARNINGS.md, kind ``lesson``) im selben Format wie Aktionen/Vorhaben:
    {kind, ts, title, why, sources, status, emil}. Neueste zuerst.

    Decision-Record-Felder (optional im JSON): ``expect`` (was soll besser werden),
    ``review`` (wann/wie wird geprüft) — landen als Quellen-Chips.
    """
    from kickbase_agent.knowledge.learnings import load_learnings
    from kickbase_agent.knowledge.parameters import load_parameter_history

    items: list[dict[str, Any]] = []
    for p in load_parameter_history():
        date = str(p.get("date") or "")
        if not date:
            continue
        src: dict[str, Any] = {"parameter": p.get("parameter", "")}
        if p.get("old") not in (None, ""):
            src["vorher"] = p["old"]
        src["nachher"] = p.get("new", "")
        for key, label in (("expect", "erwartung"), ("review", "pruefung"), ("commit", "commit")):
            if p.get(key):
                src[label] = p[key]
        arrow = f"{p.get('old')} → {p.get('new')}" if p.get("old") not in (None, "") \
            else f"neu: {p.get('new')}"
        items.append({
            "kind": "decision", "ts": f"{date}T12:00:00+02:00",
            "title": f"Parameter {p.get('parameter', '?')}: {arrow}",
            "why": str(p.get("reason") or ""), "sources": src,
            "status": "geaendert", "emil": "",
        })
    for ln in load_learnings():
        date = str(ln.get("date") or "")
        if not date:
            continue
        items.append({
            "kind": "lesson", "ts": f"{date}T12:00:00+02:00",
            "title": f"Lektion: {ln.get('title') or '?'}",
            "why": str(ln.get("text") or ""), "sources": {"quelle": "LEARNINGS"},
            "status": "gelernt", "emil": "",
        })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]
