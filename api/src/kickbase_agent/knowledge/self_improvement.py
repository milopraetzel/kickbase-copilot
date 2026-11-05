"""Selbstverbesserungen als EINE Zeitleiste — was der Agent an sich selbst ändert.

Emils Wunsch (26.08., wörtlich): „Ich hätte auch ganz gern die Sachen, die du
immer änderst oder diese Selbstverbesserungen immer ändert, dokumentiert werden
in einer Tabelle 'Learnings' in der Zentrale."

Zwei Quellen speisen dieselbe Liste:

* **Parameter-Änderungen** — Decision-Records aus ``knowledge/parameter_history.json``
  (auch die aus ``tuning.json``: ``knowledge.tuning.set_tuning`` schreibt jede
  Änderung über ``parameters.record_change`` dorthin, es gibt also nur EIN Buch).
* **Urteile des Lernzyklus** — am Audit-Eintrag vermerkt (``payload["surprise"]``,
  ``agent/judgment.py``). Der Audit-Eintrag IST der auslösende Fall; deshalb trägt
  jede Zeile ihre ``action:<id>``-Referenz.

Was hier nie passiert: raten. Ein Alt-Eintrag ohne ``quelle`` bekommt nicht
irgendeinen Autor angedichtet, sondern gilt als vom Orchestrator geschrieben —
denn genau das war er (der Selbstumbau ist dormant, ``AGENT_SELF_CHANGE=0``).
Ein kaputter Eintrag wird übersprungen, nicht repariert.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction

log = logging.getLogger(__name__)

#: Art der Zeile — die Tabelle mischt beide, die Anzeige trennt sie farblich.
KIND_PARAMETER = "parameter"
KIND_VERDICT = "urteil"

#: Wer hat geändert? Ein Schlüssel im Decision-Record (`quelle`), kein Ratespiel.
SOURCE_ORCHESTRATOR = "orchestrator"
SOURCE_SELF = "selbstumbau"
SOURCE_CYCLE = "lernzyklus"
WER: dict[str, str] = {
    SOURCE_ORCHESTRATOR: "Orchestrator",
    SOURCE_SELF: "Agent (Selbstumbau)",
    SOURCE_CYCLE: "Lernzyklus",
}

#: Präfix, mit dem der Lernzyklus seine Belege schreibt („surprise:action:39").
_SURPRISE_PREFIX = "surprise:"
#: So viele Zeilen zeigt die Zentrale ohne Aufklappen — mehr ist keine Bilanz mehr.
DEFAULT_LIMIT = 30


def _txt(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _case_from_beleg(beleg: str) -> str:
    """Auslösender Fall aus dem Beleg: „surprise:action:39" -> „action:39".

    Ein Beleg ist entweder eine Learning-ID (`L-069`), eine Ereignis-Referenz
    (`feed:…`, `action:…`) oder ein Satz Prosa. Die ersten beiden sind verlinkbar,
    der dritte bleibt stehen wie er ist — abgeschnitten wäre er wertlos.
    """
    text = beleg.strip()
    return text[len(_SURPRISE_PREFIX):].strip() if text.startswith(_SURPRISE_PREFIX) else text


def from_history(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Decision-Records -> Zeilen der Selbstverbesserungs-Tabelle."""
    rows: list[dict[str, Any]] = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        date = _txt(item.get("date"))
        name = _txt(item.get("parameter"))
        if not date or not name:
            continue                       # ohne Wann und Was ist es kein Record
        quelle = _txt(item.get("quelle")) or SOURCE_ORCHESTRATOR
        beleg = _txt(item.get("beleg"))
        rows.append({
            "kind": KIND_PARAMETER,
            "at": date,
            "quelle": quelle,
            "wer": WER.get(quelle, quelle),
            "titel": name,
            "old": _txt(item.get("old")) or None,
            "new": _txt(item.get("new")),
            "grund": _txt(item.get("reason")),
            "beleg": beleg,
            "fall": _case_from_beleg(beleg),
            "erwartung": _txt(item.get("expect")),
            "review": _txt(item.get("review")),
            "commit": _txt(item.get("commit")),
            "learning": beleg if beleg.startswith("L-") else "",
            "einzelfall_ausnahme": bool(item.get("einzelfall_ausnahme")),
        })
    return rows


def load_verdicts(session: Session, *, limit: int = 200) -> list[dict[str, Any]]:
    """Urteile des Lernzyklus aus dem Audit-Log, neueste zuerst.

    Gelesen wird ``payload["surprise"]`` — genau das, was ``judgment.py`` dort
    hinterlegt. Ist der Eintrag kein Objekt (Alt-Daten, Fremdschreiber), wird die
    Zeile übersprungen: eine halbe Zeile in einer Bilanz ist schlimmer als keine.
    """
    rows: list[dict[str, Any]] = []
    try:
        candidates = session.scalars(
            select(TransferAction).order_by(TransferAction.id.desc()).limit(limit * 5)
        ).all()
    except Exception:                       # pragma: no cover — DB darf nie den Build killen
        log.exception("Urteile nicht lesbar")
        return []
    for row in candidates:
        payload = row.payload if isinstance(row.payload, dict) else {}
        verdict = payload.get("surprise")
        if not isinstance(verdict, dict) or not _txt(verdict.get("urteil")):
            continue
        beleg = _txt(verdict.get("beleg"))
        new = verdict.get("neuer_wert")
        rows.append({
            "kind": KIND_VERDICT,
            "at": _txt(verdict.get("at")) or _stamp(row.created_at),
            "quelle": SOURCE_CYCLE,
            "wer": WER[SOURCE_CYCLE],
            "titel": _txt(verdict.get("stellschraube")) or _txt(verdict.get("urteil")),
            "urteil": _txt(verdict.get("urteil")),
            "old": None,
            "new": _txt(new) if new is not None else "",
            "grund": _txt(verdict.get("erklaerung")),
            "beleg": beleg,
            # Der Audit-Eintrag IST der Fall — er schlaegt jeden Beleg-Text.
            "fall": f"action:{row.id}",
            "erwartung": _txt(verdict.get("mechanismus")),
            "review": "",
            "commit": "",
            "learning": _txt(verdict.get("learning")),
            "einzelfall_ausnahme": bool(verdict.get("einzelfall_ausnahme")),
        })
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


def _stamp(value: Any) -> str:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat()
    return ""


def merge(*sources: list[dict[str, Any]], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Alle Quellen in EINE Zeitleiste, neueste zuerst, auf *limit* gekürzt.

    Sortiert wird über die ISO-Zeichenkette: „2026-08-26" und
    „2026-08-26T09:00:00+00:00" ordnen sich damit korrekt zueinander, ohne dass
    ein Datum ohne Uhrzeit auf Mitternacht geraten werden müsste.
    """
    rows = [r for src in sources for r in (src or []) if isinstance(r, dict) and r.get("at")]
    rows.sort(key=lambda r: str(r["at"]), reverse=True)
    return rows[:limit] if limit and limit > 0 else rows


def load_self_improvements(
    session: Session,
    *,
    history: list[dict[str, Any]] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fertige Zeitleiste für die Zentrale: Parameter-Änderungen + Urteile."""
    if history is None:
        from kickbase_agent.knowledge.parameters import load_parameter_history
        history = load_parameter_history()
    return merge(from_history(history), load_verdicts(session), limit=limit)
